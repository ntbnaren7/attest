"""Settings come from the environment (or a local .env). Credentials never live in code or prompts."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: _env("DB_PATH", str(ROOT / "data" / "receivables.db")))
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET", ""))
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8000"))

    # Cashfree PG sandbox
    cashfree_client_id: str = field(default_factory=lambda: _env("CASHFREE_CLIENT_ID"))
    cashfree_client_secret: str = field(default_factory=lambda: _env("CASHFREE_CLIENT_SECRET"))
    cashfree_base_url: str = field(default_factory=lambda: _env("CASHFREE_BASE_URL", "https://sandbox.cashfree.com/pg"))
    cashfree_api_version: str = field(default_factory=lambda: _env("CASHFREE_API_VERSION", "2026-01-01"))
    # Which tenant uses the live sandbox credentials. All other tenants use labeled fixtures.
    cashfree_live_merchant: str = field(default_factory=lambda: _env("CASHFREE_LIVE_MERCHANT", "lender_demo"))
    webhook_tolerance_seconds: int = field(default_factory=lambda: int(_env("WEBHOOK_TOLERANCE_SECONDS", "900")))

    # Sarvam
    sarvam_api_key: str = field(default_factory=lambda: _env("SARVAM_API_KEY"))
    # Second key (customer voice layer; also the fallback when the first is rate-limited)
    sarvam_voice_api_key: str = field(default_factory=lambda: _env("SARVAM_VOICE_API_KEY"))
    sarvam_base_url: str = field(default_factory=lambda: _env("SARVAM_BASE_URL", "https://api.sarvam.ai"))
    sarvam_chat_model: str = field(default_factory=lambda: _env("SARVAM_CHAT_MODEL", "sarvam-105b"))
    sarvam_stt_model: str = field(default_factory=lambda: _env("SARVAM_STT_MODEL", "saaras:v4"))
    sarvam_tts_model: str = field(default_factory=lambda: _env("SARVAM_TTS_MODEL", "bulbul:v3"))
    sarvam_tts_speaker: str = field(default_factory=lambda: _env("SARVAM_TTS_SPEAKER", "shubh"))

    # Cashfree Relay: Inbound Webhook trigger URL copied from the Relay dashboard.
    relay_webhook_url: str = field(default_factory=lambda: _env("RELAY_WEBHOOK_URL"))
    relay_webhook_secret: str = field(default_factory=lambda: _env("RELAY_WEBHOOK_SECRET"))

    # Controlled test recipients. Outreach to anything else is refused.
    test_recipients: tuple = field(default_factory=lambda: tuple(
        x.strip() for x in _env("TEST_RECIPIENTS", "9999999999,9999999998,9999999997,9999999990").split(",") if x.strip()))

    # Queue policy
    stale_minutes: int = field(default_factory=lambda: int(_env("STALE_MINUTES", "30")))
    reconcile_interval_seconds: int = field(default_factory=lambda: int(_env("RECONCILE_INTERVAL_SECONDS", "10")))
    approval_ttl_seconds: int = field(default_factory=lambda: int(_env("APPROVAL_TTL_SECONDS", "600")))
    call_window_start_hour: int = 8   # RBI recovery norms: 8 AM to 7 PM, borrower local time
    call_window_end_hour: int = 19
    lender_display_name: str = field(default_factory=lambda: _env("LENDER_DISPLAY_NAME", "Demo Finance (synthetic)"))
    grievance_contact: str = field(default_factory=lambda: _env("GRIEVANCE_CONTACT", "Grievance officer, Demo Finance, 1800-000-0000 (synthetic)"))

    @property
    def cashfree_live(self) -> bool:
        return bool(self.cashfree_client_id and self.cashfree_client_secret)

    @property
    def sarvam_live(self) -> bool:
        return bool(self.sarvam_api_key)

    @property
    def relay_mode(self) -> str:
        return "inbound_webhook" if self.relay_webhook_url else "manual"


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
