"""Cashfree Relay delivery adapter.

What the docs establish (fetched 2026-11-21): Relay agents start from a Schedule, a Cashfree
payment event, or an Inbound Webhook whose URL is generated in the Relay dashboard. No public
REST API for starting runs or reading run outcomes is documented, and the webhook auth and
payload schema are not documented.

So there are two modes, and exactly one is used per action:
  inbound_webhook  RELAY_WEBHOOK_URL is set: POST the approved, verified case packet once.
  manual           no URL: build the same packet and show it to the operator as an explicit
                   manual step ("run in Relay > Test flow"), recorded as NOT delivered by us.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx


class RelayAdapter:
    def __init__(self, webhook_url: str = "", secret: str = ""):
        self.webhook_url = webhook_url
        self.secret = secret

    @property
    def mode(self) -> str:
        return "inbound_webhook" if self.webhook_url else "manual"

    def deliver(self, packet: dict, idem_key: str) -> dict:
        body = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()
        if self.mode == "manual":
            return {"mode": "manual", "status": "awaiting_operator",
                    "instruction": "Relay has no automated trigger configured for this account. Open Relay > your agent > "
                                   "Test flow, paste this packet as sample data and Execute, then record the run ID.",
                    "packet": packet}
        headers = {"Content-Type": "application/json", "Idempotency-Key": idem_key}
        if self.secret:
            # Our own signature so the Relay agent (or a router in front of it) can check origin.
            headers["X-Receivables-Signature"] = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        try:
            r = httpx.post(self.webhook_url, content=body, headers=headers, timeout=20)
        except httpx.HTTPError as e:
            return {"mode": "inbound_webhook", "status": "failed", "error": e.__class__.__name__}
        return {"mode": "inbound_webhook", "status": "accepted" if r.status_code < 300 else "failed",
                "http_status": r.status_code, "response_excerpt": r.text[:300]}
