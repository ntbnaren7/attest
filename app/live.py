"""Live-test control panel endpoints: status, live provider checks, sample data and the payment watcher.

Everything is scoped to the signed-in operator's tenant and rate limited. Sample data can only come
from the fixture files shipped in fixtures/ (no arbitrary paths). Live checks that cost credits or
send an email are charged to the daily Sarvam budget and run only when explicitly requested.
"""
from __future__ import annotations

import base64
import time
import uuid
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import coordinator, importer, queue as queue_mod
from .config import ROOT
from .context import Ctx
from .db import iso, now
from .providers.cashfree import CashfreeLive, ProviderError
from .providers.sarvam import SarvamError, parse_tool_call
from .security import Security

SAMPLES = {
    "loan_book": ("borrower_batch_A.csv", "Loan book sample (12 people, 6 bad rows)"),
    "fresh_batch": ("borrower_batch_B_fresh.csv", "Fresh judges-style batch (new headers, IDs, traps)"),
    "supplier": ("supplier_batch.csv", "Supplier invoices sample"),
}


def register(app: FastAPI, ctx: Ctx, sec: Security, operator) -> None:
    @app.get("/api/live/status")
    def live_status(op=Depends(operator)):
        sec.limit("read", f"live:{op.id}")
        s = ctx.settings
        cf = ctx.cashfree_for(op.merchant_id)
        links = ctx.db.all(
            "SELECT p.link_id, p.status, p.amount_paise, p.amount_paid_paise, p.link_url, p.provenance, p.created_at,"
            " p.last_checked_at, a.external_ref, a.display_name, a.id AS account_id FROM payment_links p"
            " JOIN accounts a ON a.id=p.account_id WHERE p.merchant_id=? ORDER BY p.created_at DESC LIMIT 8",
            (op.merchant_id,))
        hooks = ctx.db.all(
            "SELECT received_at, signature_ok, event_type, link_id, outcome FROM webhook_events"
            " WHERE merchant_id=? OR merchant_id IS NULL ORDER BY received_at DESC LIMIT 8", (op.merchant_id,))
        receipts = ctx.db.all(
            "SELECT r.provider_payment_id, r.amount_paise, r.verified_at, r.provenance, r.link_id, a.external_ref"
            " FROM receipts r JOIN accounts a ON a.id=r.account_id WHERE r.merchant_id=?"
            " ORDER BY r.verified_at DESC LIMIT 6", (op.merchant_id,))
        # Which path caught each link payment first: a signed webhook or the reconciler's poll.
        for l in links:
            first = ctx.db.one("SELECT actor, ts FROM trace WHERE merchant_id=? AND kind='handoff' AND allowed_action='refresh_link'"
                               " AND result_json LIKE ? AND result_json LIKE '%new_receipts\": [\"%' ORDER BY id LIMIT 1",
                               (op.merchant_id, f'%{l["link_id"]}%'))
            l["caught_by"] = (first or {}).get("actor")
        public = s.public_base_url
        return {
            "now": iso(),
            "cashfree": {"mode": cf.provenance, "api_version": s.cashfree_api_version,
                         "label": "Cashfree sandbox (live API)" if isinstance(cf, CashfreeLive) else "Practice data (fixture)"},
            "sarvam": {"live": bool(ctx.sarvam), "models": [s.sarvam_chat_model, s.sarvam_stt_model, s.sarvam_tts_model],
                       "calls_today": sec.budget.used(op.merchant_id), "daily_budget": sec.budget.limit},
            "relay": {"mode": ctx.relay.mode},
            "webhooks": {"public_url": f"{public.rstrip('/')}/webhooks/cashfree" if public.startswith("https://") else None,
                         "received": ctx.db.one("SELECT COUNT(*) n FROM webhook_events WHERE signature_ok=1")["n"],
                         "rejected": ctx.db.one("SELECT COUNT(*) n FROM webhook_events WHERE signature_ok=0")["n"]},
            "watcher": {"poll_every_s": s.reconcile_interval_seconds, "last_poll_at": queue_mod.LAST_RECONCILE.get("at"),
                        "last_poll_checked": queue_mod.LAST_RECONCILE.get("checked")},
            "links": links, "webhook_events": hooks, "receipts": receipts,
            "samples": {k: v[1] for k, v in SAMPLES.items()},
            "test_upi": "testsuccess@gocash",
        }

    @app.post("/api/live/sample/{which}")
    def live_sample(which: str, op=Depends(operator)):
        if which not in SAMPLES:
            raise HTTPException(404, "unknown sample")
        sec.limit("import", op.id)
        name = SAMPLES[which][0]
        rep = importer.import_batch(ctx, op.merchant_id, name, (ROOT / "fixtures" / name).read_bytes(), f"operator:{op.username}")
        if rep.get("ok"):
            rep["queue"] = queue_mod.build_queue(ctx, op.merchant_id, rep["batch_id"])["counts"]
        return rep

    @app.post("/api/live/poll")
    def live_poll(op=Depends(operator)):
        sec.limit("provider", op.id)
        return {"checked": queue_mod.reconcile_once(ctx), "at": iso()}

    @app.post("/api/live/check")
    async def live_check(request: Request, op=Depends(operator)):
        """Real calls: a ₹1 Cashfree sandbox link, one Sarvam tool call + speech round trip, optionally one Relay email."""
        body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
        sec.limit("provider", op.id)
        sec.limit("assistant", op.id)
        results: dict = {}

        def cashfree():
            cf = ctx.cashfree_for(op.merchant_id)
            if not isinstance(cf, CashfreeLive):
                return {"ok": None, "detail": "This scenario uses practice data. The live sandbox is set for the other scenario."}
            t0 = time.time()
            try:
                st = cf.create_link(link_id=f"check_{uuid.uuid4().hex[:10]}", amount_paise=100, customer_phone="9999999999",
                                    customer_name="Live check", customer_email=None, purpose="Live check (synthetic)",
                                    expiry=now() + timedelta(days=1), notes={"synthetic": "true"}, notify_url=None,
                                    return_url=None, idem_key=str(uuid.uuid4()), allow_partial=False)
                back = cf.get_link(st.link_id)
                return {"ok": True, "detail": f"Created and read back a ₹1 test link ({back.status})",
                        "ms": int((time.time() - t0) * 1000)}
            except ProviderError as e:
                return {"ok": False, "detail": f"{e.kind}: {e}"}

        def sarvam():
            if not ctx.sarvam:
                return {"ok": None, "detail": "No Sarvam key configured"}
            sec.budget.charge(op.merchant_id, 3)
            t0 = time.time()
            try:
                r = ctx.sarvam.chat([{"role": "system", "content": coordinator.SYSTEM.format(today=now().date().isoformat())},
                                     {"role": "user", "content": "L-1001 के लिए पचास हज़ार नहीं, पाँच हज़ार रुपये का लिंक भेजिए"}],
                                    tools=[coordinator.TOOL],
                                    tool_choice={"type": "function", "function": {"name": "route_operator_request"}})
                name, args = parse_tool_call(r["message"])
                tts = ctx.sarvam._tts_uncached("L-1001 पर चार हज़ार दो सौ रुपये बकाया है।", "hi-IN")
                stt = ctx.sarvam.stt(base64.b64decode(tts["audio_b64"] or ""), "check.wav", "audio/wav", "unknown")
                ok = name == "route_operator_request" and str((args or {}).get("amount_inr", "")).startswith("5000")
                return {"ok": ok, "ms": int((time.time() - t0) * 1000),
                        "detail": f"Tool call {name}: intent {(args or {}).get('intent')}, amount ₹{(args or {}).get('amount_inr')}. "
                                  f"Speech round trip heard: “{stt['transcript']}”"}
            except SarvamError as e:
                return {"ok": False, "detail": str(e)[:200]}

        def relay():
            if not body.get("relay"):
                return {"ok": None, "detail": "Skipped (tick 'send Relay test email' to include it)"}
            if ctx.relay.mode != "inbound_webhook":
                return {"ok": None, "detail": "No Relay webhook URL configured"}
            from .specialists import email_template_fields
            p = {"case_id": "live_check", "account_ref": "LIVE-CHECK", "link_id": "check", "link_url": "https://payments-test.cashfree.com",
                 "approved_amount_inr": "₹1", "amount_paise": 100, "recipient": "9999999999", "language": "en-IN",
                 "allowed_action": "operator_alert_test", "test_mode": True}
            p.update(email_template_fields(p))
            res = ctx.relay.deliver(p, f"live-check-{uuid.uuid4().hex[:8]}")
            return {"ok": res["status"] == "accepted", "detail": f"Relay {res['status']} {res.get('response_excerpt', '')}".strip()}

        results["cashfree"] = await run_in_threadpool(cashfree)
        results["sarvam"] = await run_in_threadpool(sarvam)
        results["relay"] = await run_in_threadpool(relay)
        results["at"] = iso()
        return results
