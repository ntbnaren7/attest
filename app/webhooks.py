"""Cashfree webhook intake.

1. Verify base64(HMAC-SHA256(timestamp + raw_body)) with the secret of the tenant that owns the link,
   on the exact raw bytes, inside a replay window.
2. Deduplicate (x-idempotency-key, else body hash). Duplicates and replays change nothing.
3. Never trust the payload's amounts or status: re-read the link, its orders and payments from the
   provider. That makes out-of-order delivery harmless (late FAILED after SUCCESS cannot reopen).
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs

from .context import Ctx
from .db import iso, new_id
from .providers.cashfree import verify_webhook_signature
from .queue import build_queue
from .specialists import allocate_receipts, handoff


def _parse(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        pass
    try:  # docs mention form-encoded delivery; accept a flat form as well
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8")).items()}
        return {"type": form.get("type"), "data": form}
    except Exception:
        return {}


def _hints(body: dict) -> tuple[str | None, str | None, str | None]:
    data = body.get("data") or {}
    order = data.get("order") or {}
    tags = order.get("order_tags") or {}
    link_id = data.get("link_id") or (tags.get("link_id") if isinstance(tags, dict) else None)
    return body.get("type"), link_id, order.get("order_id")


def process(ctx: Ctx, raw: bytes, headers: dict) -> tuple[int, dict]:
    db = ctx.db
    h = {k.lower(): v for k, v in headers.items()}
    ts, sig = h.get("x-webhook-timestamp"), h.get("x-webhook-signature")
    body = _parse(raw)
    etype, link_id, order_id = _hints(body)
    link = None
    if link_id:
        link = db.one("SELECT * FROM payment_links WHERE link_id=?", (link_id,))
    if not link and order_id:
        rc = db.one("SELECT * FROM receipts WHERE order_id=?", (order_id,))
        if rc and rc.get("link_id"):
            link = db.one("SELECT * FROM payment_links WHERE link_id=?", (rc["link_id"],))
    merchant_id = link["merchant_id"] if link else ctx.settings.cashfree_live_merchant
    secret = ctx.cashfree_for(merchant_id).webhook_secret
    ok, why = verify_webhook_signature(raw, ts, sig, secret, ctx.settings.webhook_tolerance_seconds)
    body_hash = hashlib.sha256(raw).hexdigest()
    if not ok:
        db.x("INSERT OR IGNORE INTO webhook_events (id, dedupe_key, merchant_id, signature_ok, event_type, link_id, order_id,"
             " received_at, outcome, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
             (new_id("wh"), f"rejected:{body_hash}:{ts}", merchant_id, 0, etype, link_id, order_id, iso(),
              f"rejected: {why}", raw[:2000].decode("utf-8", "replace")))
        db.trace(merchant_id, "webhook_rejected", "cashfree_webhook", account_id=link["account_id"] if link else None,
                 result={"reason": why, "type": etype, "link_id": link_id})
        return 401, {"status": "rejected", "reason": why}
    dedupe = h.get("x-idempotency-key") or f"sha:{body_hash}"
    cur = db.x("INSERT OR IGNORE INTO webhook_events (id, dedupe_key, merchant_id, signature_ok, event_type, link_id, order_id,"
               " received_at, outcome, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (new_id("wh"), dedupe, merchant_id, 1, etype, link_id, order_id, iso(), "received",
                raw[:4000].decode("utf-8", "replace")))
    if cur.rowcount == 0:
        db.trace(merchant_id, "webhook_duplicate", "cashfree_webhook", account_id=link["account_id"] if link else None,
                 result={"dedupe_key": dedupe, "type": etype})
        return 200, {"status": "duplicate_ignored"}
    if not link:
        db.x("UPDATE webhook_events SET outcome=? WHERE dedupe_key=?", ("ignored: link not issued by this system", dedupe))
        return 200, {"status": "ignored_unknown_link"}
    res = handoff(ctx, link["merchant_id"], link["account_id"], "evidence", "refresh_link", actor="cashfree_webhook",
                  link_id=link["link_id"])
    allocate_receipts(ctx, link["merchant_id"], link["account_id"])
    q = build_queue(ctx, link["merchant_id"])
    outcome = (f"{why}; content-type={h.get('content-type', '?')}; version={h.get('x-webhook-version', '?')}; "
               f"attempt={h.get('x-webhook-attempt', '?')}; refreshed: {res.summary}")
    db.x("UPDATE webhook_events SET outcome=? WHERE dedupe_key=?", (outcome, dedupe))
    return 200, {"status": "processed", "refresh": res.as_dict(), "queue_changes": q["changes"]}
