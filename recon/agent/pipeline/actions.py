"""Side effects. Only called for findings a human approved. Dry-run unless execute=True.

Executable today (sandbox-verified APIs): create a payment link to collect an amount reconciliation proved
unpaid, and send an approved payout. Everything else is advisory (a task for ops), by design. Retrying ordinary
failed payments is Cashfree Relay's job and is intentionally not here.
"""
from ..sources import cashfree as cfio

cf = cfio.cf

def _stable(prefix, *parts):
    import hashlib
    return f"{prefix}_" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:20]


EXECUTABLE = {"PAYMENT_LINK", "SCHEDULE_NEXT_DAY", "APPROVE"}


def plan(f, ledger):
    """Describe exactly what would be sent, so the approver sees it before it happens."""
    a = f["action"]
    if a in ("PAYMENT_LINK", "SCHEDULE_NEXT_DAY"):
        cust = f["evidence"].get("customer") or ""
        return dict(api="POST /pg/links", amount=f["amount"], customer=cust, purpose=f"Payment for {f['entity']}",
                    send_after="next day 10:00" if a == "SCHEDULE_NEXT_DAY" else "now")
    if a == "APPROVE":
        q = next(q for q in ledger["payout_requests"] if q["request_id"] == f["entity"])
        return dict(api="POST /payout/transfers", payee=q["payee"], amount=q["amount"], beneficiary_id=q.get("beneficiary_id"),
                    vpa=q.get("vpa"), remarks=f"{q.get('invoice', '')} {q['payee']}")
    return dict(api=None, note="advisory: create an ops task")


def execute(f, ledger, execute=False):
    p = plan(f, ledger)
    if f["action"] not in EXECUTABLE or not p.get("api"):
        return dict(status="ADVISORY", plan=p)
    if not execute:
        return dict(status="DRY_RUN", plan=p)
    try:
        a = f["action"]
        if a in ("PAYMENT_LINK", "SCHEDULE_NEXT_DAY"):
            # deterministic id: approving the same recovery twice cannot create two links (Cashfree returns 409)
            import os
            hook = os.getenv("PUBLIC_URL", "").rstrip("/")
            r = cf.create_payment_link(f["amount"], "9999999999", p["purpose"], p["customer"] or None, link_id=_stable("rcv", f["entity"]),
                                       notify_url=f"{hook}/webhook/cashfree" if hook else None)
            return dict(status="LINK_CREATED", plan=p, link_id=r.get("link_id"), link_url=r.get("link_url"))
        if a == "APPROVE":
            bid = p["beneficiary_id"]
            if not bid:
                bid = cf.create_beneficiary(p["payee"], vpa=p["vpa"])["beneficiary_id"]
            # deterministic id: a re-run or double-click can never pay the same request twice
            r = cf.transfer(p["amount"], bid, mode="upi" if p["vpa"] else "imps", remarks=p["remarks"], transfer_id=_stable("po", f["entity"], ledger["run"]))
            return dict(status="PAYOUT_SUBMITTED", plan=p, transfer_id=r.get("transfer_id"), cf_status=r.get("status"))
    except cf.CashfreeError as e:
        body = e.body if isinstance(e.body, dict) else {}
        if e.status == 409 or "already exists" in str(body.get("message", "")).lower():  # links: 400 link_post_failed
            return dict(status="ALREADY_DONE", plan=p, note="idempotency: this action was executed before")
        return dict(status="ERROR", plan=p, error=str(e)[:300])
    return dict(status="ADVISORY", plan=p)
