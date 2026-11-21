"""Abuse and cost protection.

Everything here is in-process (single server, no extra services):
  * token-bucket rate limits keyed by operator or client IP, per route class
  * a daily Sarvam call budget per tenant (LLM, speech-to-text, text-to-speech all cost credits)
  * one in-flight assistant request per operator (no parallel fan-out of model calls)
  * login lockout after repeated failures
  * security headers, request-size caps and a same-origin check for state-changing API calls
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("receivables.security")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Limit:
    per_minute: float
    burst: int


# Route classes. Sarvam-backed routes are the most expensive, so the tightest.
LIMITS: dict[str, Limit] = {
    "login": Limit(per_minute=_env_int("RL_LOGIN_PER_MIN", 10), burst=5),
    "assistant": Limit(per_minute=_env_int("RL_ASSISTANT_PER_MIN", 12), burst=4),     # LLM + STT + TTS
    "speech": Limit(per_minute=_env_int("RL_SPEECH_PER_MIN", 30), burst=8),           # TTS for explain/help
    "provider": Limit(per_minute=_env_int("RL_PROVIDER_PER_MIN", 30), burst=10),      # Cashfree reads
    "money": Limit(per_minute=_env_int("RL_MONEY_PER_MIN", 12), burst=4),             # link request/approve/execute
    "import": Limit(per_minute=_env_int("RL_IMPORT_PER_MIN", 6), burst=3),
    "webhook": Limit(per_minute=_env_int("RL_WEBHOOK_PER_MIN", 300), burst=60),       # per source IP
    "read": Limit(per_minute=_env_int("RL_READ_PER_MIN", 240), burst=60),             # queue polling etc.
}

# Body size caps by path prefix (bytes). Anything else falls back to DEFAULT_MAX_BODY.
MAX_BODY = {
    "/api/import": 5_000_000,
    "/api/coordinator/voice": 1_500_000,   # ~30 s of compressed speech
    "/api/inbox/message": 1_600_000,       # customer voice note or photo plus form fields
    "/api/ledger/scan": 1_600_000,
    "/webhooks/": 64_000,
}
DEFAULT_MAX_BODY = 32_000


class RateLimiter:
    def __init__(self):
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, kind: str, key: str) -> float:
        """Consume one token. Returns 0 if allowed, else seconds until a token is available."""
        lim = LIMITS[kind]
        rate = lim.per_minute / 60.0
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get((kind, key), (float(lim.burst), now))
            tokens = min(float(lim.burst), tokens + (now - last) * rate)
            if tokens >= 1:
                self._buckets[(kind, key)] = (tokens - 1, now)
                return 0.0
            self._buckets[(kind, key)] = (tokens, now)
            return (1 - tokens) / rate if rate > 0 else 60.0


class DailyBudget:
    """Counts paid model calls per tenant per UTC day."""

    def __init__(self, limit: int):
        self.limit = limit
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = threading.Lock()

    def charge(self, tenant: str, calls: int) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            used = self._counts[(tenant, day)]
            if used + calls > self.limit:
                raise HTTPException(429, f"Daily voice/AI budget reached ({self.limit} calls). Try again tomorrow or "
                                         "raise SARVAM_DAILY_CALL_BUDGET.", headers={"Retry-After": "3600"})
            self._counts[(tenant, day)] = used + calls

    def used(self, tenant: str) -> int:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._counts[(tenant, day)]


class LoginGuard:
    """Locks a username after repeated failures (independent of source IP)."""

    def __init__(self, max_failures: int = 5, window_s: int = 900, lock_s: int = 900):
        self.max_failures, self.window_s, self.lock_s = max_failures, window_s, lock_s
        self._fails: dict[str, list[float]] = defaultdict(list)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, username: str) -> None:
        with self._lock:
            until = self._locked_until.get(username.lower(), 0)
        if until > time.monotonic():
            raise HTTPException(429, "Too many failed sign-ins. Try again in a few minutes.",
                                headers={"Retry-After": str(int(until - time.monotonic()) + 1)})

    def failed(self, username: str) -> None:
        now = time.monotonic()
        u = username.lower()
        with self._lock:
            self._fails[u] = [t for t in self._fails[u] if now - t < self.window_s] + [now]
            if len(self._fails[u]) >= self.max_failures:
                self._locked_until[u] = now + self.lock_s
                self._fails[u] = []
                log.warning("login locked for %s", u)

    def succeeded(self, username: str) -> None:
        with self._lock:
            self._fails.pop(username.lower(), None)


class InFlight:
    """At most one assistant request per operator at a time."""

    def __init__(self):
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def acquire(self, key: str) -> None:
        with self._lock:
            if key in self._busy:
                raise HTTPException(429, "Still working on your last request. Please wait for the answer.",
                                    headers={"Retry-After": "2"})
            self._busy.add(key)

    def release(self, key: str) -> None:
        with self._lock:
            self._busy.discard(key)


class Security:
    def __init__(self):
        self.limiter = RateLimiter()
        self.budget = DailyBudget(_env_int("SARVAM_DAILY_CALL_BUDGET", 1500))
        self.logins = LoginGuard(max_failures=_env_int("LOGIN_MAX_FAILURES", 5))
        self.inflight = InFlight()

    def limit(self, kind: str, key: str) -> None:
        wait = self.limiter.check(kind, key)
        if wait > 0:
            log.info("rate limited kind=%s key=%s wait=%.1fs", kind, key, wait)
            raise HTTPException(429, "Too many requests. Please wait a moment and try again.",
                                headers={"Retry-After": str(int(wait) + 1)})


def client_ip(request: Request) -> str:
    # Behind a tunnel or proxy the left-most forwarded address is the client; fall back to the socket peer.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; media-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'")


class SecurityMiddleware(BaseHTTPMiddleware):
    """Headers on every response; size cap, origin check and a coarse per-IP limit on requests."""

    def __init__(self, app, security: Security, allowed_origins: set[str]):
        super().__init__(app)
        self.sec = security
        self.allowed_origins = allowed_origins

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 1. Body size: reject before reading anything large.
        cap = next((v for k, v in MAX_BODY.items() if path.startswith(k)), DEFAULT_MAX_BODY)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > cap:
            return JSONResponse({"error": "TOO_LARGE", "detail": f"Request too large (limit {cap // 1000} KB)."}, 413)
        # 2. State-changing API calls must come from our own pages (cookies are SameSite=strict as well).
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in self.allowed_origins and origin.rstrip("/") != _self_origin(request):
                return JSONResponse({"error": "BAD_ORIGIN", "detail": "Cross-site request blocked."}, 403)
        # 3. Coarse per-IP ceiling across the API and webhooks.
        if path.startswith("/webhooks/"):
            kind = "webhook"
        elif path.startswith("/api/"):
            kind = "read"
        else:
            kind = None
        if kind:
            wait = self.sec.limiter.check(kind, client_ip(request))
            if wait > 0:
                return JSONResponse({"error": "RATE_LIMITED", "detail": "Too many requests. Please slow down."}, 429,
                                    headers={"Retry-After": str(int(wait) + 1)})
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if not path.startswith("/api/") and not path.startswith("/webhooks/"):
            h.setdefault("Content-Security-Policy", CSP)
        if path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        if is_https(request):
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response


def _self_origin(request: Request) -> str:
    scheme = "https" if is_https(request) else request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{scheme}://{host}"
