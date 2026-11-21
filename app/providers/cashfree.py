"""Cashfree PG adapter: live sandbox client and a labeled fixture twin with the same interface.

Contracts (docs fetched 2026-11-21, x-api-version 2026-01-01):
  POST /links, GET /links/{id}, GET /links/{id}/orders?status=ALL, GET /orders/{id}/payments
  Webhook signature = base64(HMAC_SHA256(x-webhook-timestamp + raw_body, client_secret))
A webhook is only ever a hint: the backend re-reads provider state before changing anything.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from ..db import DB, iso, now
from ..money import paise_to_rupee_str, provider_amount_to_paise

FIXTURE_WEBHOOK_SECRET = "fixture-webhook-secret-not-a-credential"


class ProviderError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind  # not_found | unavailable | auth | bad_request | bad_response | idempotency
        self.status = status


@dataclass
class LinkState:
    link_id: str
    link_url: str | None
    status: str
    amount_paise: int
    amount_paid_paise: int
    provenance: str
    raw: dict


@dataclass
class Payment:
    cf_payment_id: str
    order_id: str
    payment_status: str
    amount_paise: int
    payment_time: str | None
    provenance: str


def verify_webhook_signature(raw_body: bytes, timestamp: str | None, signature: str | None, secret: str,
                             tolerance_seconds: int, at: datetime | None = None) -> tuple[bool, str]:
    if not timestamp or not signature:
        return False, "missing signature headers"
    # Code samples sign timestamp+body; the docs' pseudo-code shows timestamp.body. Accept either, report which.
    variant = None
    for name, msg in (("concat", timestamp.encode() + raw_body), ("dot", timestamp.encode() + b"." + raw_body)):
        expected = base64.b64encode(hmac.new(secret.encode(), msg, hashlib.sha256).digest()).decode()
        if hmac.compare_digest(expected, signature.strip()):
            variant = name
            break
    if not variant:
        return False, "signature mismatch"
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "timestamp not numeric"
    ts_s = ts / 1000 if ts > 10**12 else ts  # Cashfree sends milliseconds; accept seconds too
    at = at or now()
    if abs(at.timestamp() - ts_s) > tolerance_seconds:
        return False, "timestamp outside replay window"
    return True, f"ok (signature form: {variant})"


def sign_webhook(raw_body: bytes, timestamp: str, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), timestamp.encode() + raw_body, hashlib.sha256).digest()).decode()


def _link_state(d: dict, provenance: str) -> LinkState:
    try:
        return LinkState(
            link_id=d["link_id"], link_url=d.get("link_url"), status=str(d.get("link_status", "")).upper(),
            amount_paise=provider_amount_to_paise(d["link_amount"]),
            amount_paid_paise=provider_amount_to_paise(d.get("link_amount_paid") or 0),
            provenance=provenance, raw=d)
    except (KeyError, ValueError) as e:
        raise ProviderError("bad_response", f"link response missing/invalid field: {e}") from e


class CashfreeLive:
    provenance = "cashfree_sandbox"

    def __init__(self, base_url: str, client_id: str, client_secret: str, api_version: str, timeout: float = 20):
        self.base_url = base_url.rstrip("/")
        self.webhook_secret = client_secret
        self._client = httpx.Client(timeout=timeout, headers={
            "x-client-id": client_id, "x-client-secret": client_secret,
            "x-api-version": api_version, "Content-Type": "application/json", "Accept": "application/json"})

    def _req(self, method: str, path: str, *, json_body=None, params=None, idem_key: str | None = None):
        headers = {"x-request-id": str(uuid.uuid4())}
        if idem_key:
            headers["x-idempotency-key"] = idem_key
        last = None
        for attempt in range(3):
            try:
                r = self._client.request(method, self.base_url + path, json=json_body, params=params, headers=headers)
            except httpx.HTTPError as e:
                last = ProviderError("unavailable", f"network error: {e.__class__.__name__}")
                # Retrying a create is safe only because the idempotency key is fixed.
                if method != "GET" and not idem_key:
                    raise last
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and (method == "GET" or idem_key):
                last = ProviderError("unavailable", f"HTTP {r.status_code}", r.status_code)
                time.sleep(min(float(r.headers.get("x-ratelimit-retry", 1) or 1), 3))
                continue
            if r.status_code == 404:
                raise ProviderError("not_found", _msg(r), 404)
            if r.status_code in (401, 403):
                raise ProviderError("auth", _msg(r), r.status_code)
            if r.status_code == 422 and "idempot" in r.text.lower():
                raise ProviderError("idempotency", _msg(r), 422)
            if r.status_code >= 400:
                raise ProviderError("bad_request", _msg(r), r.status_code)
            try:
                return r.json()
            except ValueError as e:
                raise ProviderError("bad_response", "non-JSON response") from e
        raise last or ProviderError("unavailable", "provider unavailable")

    def create_link(self, *, link_id, amount_paise, customer_phone, customer_name, customer_email, purpose,
                    expiry, notes, notify_url, return_url, idem_key, allow_partial=True, min_partial_paise=None) -> LinkState:
        body = {
            "link_id": link_id, "link_amount": float(paise_to_rupee_str(amount_paise)), "link_currency": "INR",
            "link_purpose": purpose[:500],
            "customer_details": {"customer_phone": customer_phone, "customer_name": customer_name or "Test payer",
                                 **({"customer_email": customer_email} if customer_email else {})},
            "link_partial_payments": bool(allow_partial),
            "link_expiry_time": expiry.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec="seconds"),
            # Delivery happens through exactly one controlled path (Relay or the operator screen), never Cashfree SMS/email.
            "link_notify": {"send_sms": False, "send_email": False},
            "link_auto_reminders": False,
            "link_notes": {k: str(v)[:100] for k, v in list(notes.items())[:5]},
            "link_meta": {k: v for k, v in {"notify_url": notify_url if notify_url and notify_url.startswith("https://") else None,
                                            "return_url": return_url}.items() if v},
        }
        if allow_partial and min_partial_paise:
            body["link_minimum_partial_amount"] = float(paise_to_rupee_str(min_partial_paise))
        return _link_state(self._req("POST", "/links", json_body=body, idem_key=idem_key), self.provenance)

    def get_link(self, link_id: str) -> LinkState:
        return _link_state(self._req("GET", f"/links/{link_id}"), self.provenance)

    def get_link_orders(self, link_id: str) -> list[dict]:
        data = self._req("GET", f"/links/{link_id}/orders", params={"status": "ALL"})
        if not isinstance(data, list):
            raise ProviderError("bad_response", "orders response is not a list")
        return [{"order_id": o.get("order_id"), "order_status": o.get("order_status")} for o in data]

    def get_order_payments(self, order_id: str) -> list[Payment]:
        data = self._req("GET", f"/orders/{order_id}/payments")
        if not isinstance(data, list):
            raise ProviderError("bad_response", "payments response is not a list")
        out = []
        for p in data:
            try:
                out.append(Payment(str(p["cf_payment_id"]), order_id, str(p.get("payment_status", "")).upper(),
                                   provider_amount_to_paise(p["payment_amount"]),
                                   p.get("payment_completion_time") or p.get("payment_time"), self.provenance))
            except (KeyError, ValueError) as e:
                raise ProviderError("bad_response", f"payment missing field {e}") from e
        return out


def _msg(r: httpx.Response) -> str:
    try:
        j = r.json()
        return f"HTTP {r.status_code}: {j.get('message') or j.get('code') or j}"
    except ValueError:
        return f"HTTP {r.status_code}"


class CashfreeFixture:
    """Deterministic stand-in. Every object it returns is labeled provenance='fixture'."""
    provenance = "fixture"
    webhook_secret = FIXTURE_WEBHOOK_SECRET

    def __init__(self, db: DB, merchant_id: str, static_orders: dict | None = None):
        self.db = db
        self.merchant_id = merchant_id
        self.static_orders = static_orders or {}
        self.fail_next: str | None = None  # tests can inject 'unavailable'

    def _maybe_fail(self):
        if self.fail_next:
            kind, self.fail_next = self.fail_next, None
            raise ProviderError(kind, f"injected {kind}")

    def create_link(self, *, link_id, amount_paise, idem_key, **kw) -> LinkState:
        self._maybe_fail()
        existing = self.db.one("SELECT * FROM sim_links WHERE idem_key=?", (idem_key,))
        if existing:
            return self.get_link(existing["link_id"])
        body = {"link_id": link_id, "link_amount": paise_to_rupee_str(amount_paise), "purpose": kw.get("purpose"),
                "customer_phone": kw.get("customer_phone"), "notes": kw.get("notes")}
        self.db.x("INSERT INTO sim_links (link_id, merchant_id, body_json, amount_paise, status, created_at, idem_key)"
                  " VALUES (?,?,?,?,?,?,?)", (link_id, self.merchant_id, json.dumps(body), amount_paise, "ACTIVE", iso(), idem_key))
        return self.get_link(link_id)

    def get_link(self, link_id: str) -> LinkState:
        self._maybe_fail()
        r = self.db.one("SELECT * FROM sim_links WHERE link_id=? AND merchant_id=?", (link_id, self.merchant_id))
        if not r:
            raise ProviderError("not_found", "link not found", 404)
        raw = {"link_id": link_id, "link_status": r["status"], "link_amount": paise_to_rupee_str(r["amount_paise"]),
               "link_amount_paid": paise_to_rupee_str(r["amount_paid_paise"]),
               "link_url": f"/sim/pay/{link_id}", "_fixture": True}
        return _link_state(raw, self.provenance)

    def get_link_orders(self, link_id: str) -> list[dict]:
        self._maybe_fail()
        rows = self.db.all("SELECT DISTINCT order_id FROM sim_payments WHERE link_id=? AND merchant_id=?",
                           (link_id, self.merchant_id))
        return [{"order_id": r["order_id"], "order_status": "PAID"} for r in rows]

    def get_order_payments(self, order_id: str) -> list[Payment]:
        self._maybe_fail()
        rows = self.db.all("SELECT * FROM sim_payments WHERE order_id=? AND merchant_id=?", (order_id, self.merchant_id))
        if rows:
            return [Payment(r["cf_payment_id"], order_id, r["payment_status"], r["amount_paise"], r["payment_time"],
                            self.provenance) for r in rows]
        static = self.static_orders.get(order_id)
        if static is None:
            raise ProviderError("not_found", "order not found", 404)
        return [Payment(p["cf_payment_id"], order_id, p["payment_status"], int(p["amount_paise"]),
                        p.get("payment_time"), self.provenance) for p in static]

    # --- simulation helpers (fixture mode only) ---
    def simulate_payment(self, link_id: str, amount_paise: int, status: str = "SUCCESS") -> dict:
        r = self.db.one("SELECT * FROM sim_links WHERE link_id=? AND merchant_id=?", (link_id, self.merchant_id))
        if not r:
            raise ProviderError("not_found", "link not found", 404)
        remaining = r["amount_paise"] - r["amount_paid_paise"]
        if status == "SUCCESS" and (amount_paise <= 0 or amount_paise > remaining):
            raise ProviderError("bad_request", "amount exceeds link remainder")
        order_id = f"order_sim_{uuid.uuid4().hex[:10]}"
        pid = f"sim_{uuid.uuid4().hex[:12]}"
        with self.db.tx():
            self.db.x("INSERT INTO sim_payments VALUES (?,?,?,?,?,?,?)",
                      (pid, self.merchant_id, order_id, link_id, amount_paise, status, iso()))
            if status == "SUCCESS":
                paid = r["amount_paid_paise"] + amount_paise
                new_status = "PAID" if paid >= r["amount_paise"] else "PARTIALLY_PAID"
                self.db.x("UPDATE sim_links SET amount_paid_paise=?, status=? WHERE link_id=?", (paid, new_status, link_id))
        link = self.get_link(link_id)
        return {"type": "PAYMENT_LINK_EVENT", "event_time": iso(),
                "data": {"link_id": link_id, "link_status": link.status,
                         "link_amount": paise_to_rupee_str(link.amount_paise),
                         "link_amount_paid": paise_to_rupee_str(link.amount_paid_paise),
                         "order": {"order_id": order_id, "transaction_id": pid, "transaction_status": status,
                                   "order_amount": paise_to_rupee_str(amount_paise)}}}

    def expire_link(self, link_id: str) -> None:
        self.db.x("UPDATE sim_links SET status='EXPIRED' WHERE link_id=? AND merchant_id=? AND status IN ('ACTIVE','PARTIALLY_PAID')",
                  (link_id, self.merchant_id))
