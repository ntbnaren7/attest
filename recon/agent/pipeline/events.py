"""Real-time Cashfree webhook handling: what an event means for the merchant's BOOKS and MONEY.

Relay (Cashfree's agent builder) already reacts to failed payments with customer outreach and has no settlement
triggers. This module does the bookkeeping side, in real time:
  * PAYMENT_FAILED / USER_DROPPED -> structured failure diagnosis (retriable? could the customer have been debited?
    which deadline applies?) and, if the books already shipped the order, an immediate FULFILLED_BUT_UNPAID alert
  * PAYMENT_SUCCESS on an order the books show unpaid -> PAID_NOT_FULFILLED alert
  * REFUND_STATUS (SUCCESS) -> duplicate / over-refund check on that order, immediately
  * SETTLEMENT_FAILED / SETTLEMENT_REVERSED -> critical alert; SETTLEMENT_SUCCESS -> expected bank credit, triggers recon
  * DISPUTE_* -> warns that a chargeback deduction may hit the next settlement while the books still count the sale

Security (WebhookGuard): HMAC-SHA256 signature over timestamp+body (Cashfree spec), a replay window on the signed
timestamp, and duplicate-delivery suppression (Cashfree retries; v2 payment webhooks also arrive twice).
"""
import base64
import hashlib
import hmac
import time
from collections import OrderedDict
from datetime import datetime

from ..lib.dates import IST
from ..lib.deadlines import deadline, outcome_unknown
from ..lib.findings import Finding
from ..lib.money import inr, num, paise
from ..lib.taxonomy import RETRIABLE, classify

REPLAY_WINDOW_S = 300


class WebhookGuard:
    def __init__(self, secrets, window_s=REPLAY_WINDOW_S, remember=5000):
        self.secrets = [s for s in secrets if s]
        self.window_s = window_s
        self.seen = OrderedDict()
        self.remember = remember

    def check(self, raw, ts, sig, now=None):
        """Returns (ok, reason). raw: body text; ts: x-webhook-timestamp (epoch ms); sig: x-webhook-signature."""
        if not any(hmac.compare_digest(base64.b64encode(hmac.new(s.encode(), (ts + raw).encode(), hashlib.sha256).digest()).decode(),
                                       sig or "") for s in self.secrets):
            return False, "bad signature"
        try:
            t = int(ts) / (1000 if len(ts) > 11 else 1)
        except (TypeError, ValueError):
            return False, "bad timestamp"
        if abs((now or time.time()) - t) > self.window_s:
            return False, "stale timestamp (possible replay)"
        h = hashlib.sha256((ts + raw).encode()).hexdigest()
        if h in self.seen:
            return False, "duplicate delivery"
        self.seen[h] = True
        if len(self.seen) > self.remember:
            self.seen.popitem(last=False)
        return True, "ok"


def diagnose_failure(payment, error):
    """Structured failure diagnosis from a Cashfree payment + error_details object."""
    reason = " ".join(str(error.get(k) or "") for k in ("error_code", "error_reason", "error_description"))
    cls, why = classify(error.get("error_code"), reason, error.get("error_source"))
    ev = dict(gateway_status=payment.get("payment_status"), error=error, failure_class=cls)
    unknown = outcome_unknown(ev)
    return dict(failure_class=cls, classified_by=why, retriable=cls in RETRIABLE, customer_may_be_debited=unknown,
                error_code=error.get("error_code"), error_reason=error.get("error_reason"), error_source=error.get("error_source"),
                bank_said=(error.get("error_description_raw") or error.get("error_description") or "").strip(),
                raw_code=error.get("error_code_raw"),
                meaning=("Outcome unknown: the customer may have been debited. Check the status before asking again; "
                         "an unconfirmed debit must be reversed by T+5 (RBI)." if unknown else
                         "Retriable (bank/network side)." if cls in RETRIABLE else
                         "Customer must act (funds / PIN / declined)." if cls == "ASK_CUSTOMER" else
                         "Permanent: retrying will not work." if cls == "NEVER_RETRY" else "Unclassified: needs a human."))


def handle(event, books=None, refunds_by_order=None, now=None):
    """event: parsed Cashfree webhook JSON. books: {order_id: order dict from the merchant ledger}.
    Returns dict(type, order_id, diagnosis, findings: [Finding], trigger_recon: bool)."""
    now = now or datetime.now(IST)
    books = books or {}
    et = (event.get("type") or "").upper(); d = event.get("data") or {}
    order = d.get("order") or {}; pay = d.get("payment") or {}; err = d.get("error_details") or {}
    oid = order.get("order_id"); cust = (d.get("customer_details") or {}).get("customer_name") or ""
    out = dict(type=et, order_id=oid, diagnosis=None, findings=[], trigger_recon=False)
    b = books.get(oid)

    def add(kind, entity, amount, sev, summary, evidence, action, money_key):
        f = Finding("recon", kind, entity, amount, sev, summary, evidence, action=action, priority=amount, money_key=money_key)
        f.deadline = deadline(kind, evidence, now) or deadline(kind, dict(evidence, anchor_time=now.isoformat()), now)  # unreadable event time: anchor now
        out["findings"].append(f)

    if et in ("PAYMENT_FAILED_WEBHOOK", "PAYMENT_USER_DROPPED_WEBHOOK"):
        if et == "PAYMENT_USER_DROPPED_WEBHOOK" and not err:
            err = dict(error_code="USER_DROPPED", error_description="customer dropped the payment", error_source="customer")
        dg = diagnose_failure(dict(pay, payment_status=pay.get("payment_status") or ("USER_DROPPED" if "DROPPED" in et else "FAILED")), err)
        out["diagnosis"] = dg
        if b and b.get("ledger_status") == "PAID":  # the books already shipped this order
            ev = dict(gateway_status=pay.get("payment_status") or "FAILED", error=err, failure_class=dg["failure_class"],
                      customer=b.get("customer") or cust, anchor_time=pay.get("payment_time") or event.get("event_time"), source="webhook")
            add("FULFILLED_BUT_UNPAID", oid, b["amount"], "critical",
                f"Real time: books say {oid} is PAID and shipped, but Cashfree just reported the payment {dg['failure_class']} "
                f"({dg['bank_said'] or dg['error_code']}): {inr(b['amount'])} not collected. {dg['meaning']}",
                ev, "CHASE_OR_HOLD_DELIVERY", f"order:{oid}:collect")
    elif et == "PAYMENT_SUCCESS_WEBHOOK":
        if b and b.get("ledger_status") != "PAID":
            amt = num(pay.get("payment_amount") or order.get("order_amount"))
            add("PAID_NOT_FULFILLED", oid, amt, "high",
                f"Real time: Cashfree confirmed {inr(amt)} paid on {oid}, but the books still show {b.get('ledger_status')}: fulfil it.",
                dict(customer=b.get("customer") or cust, anchor_time=pay.get("payment_time") or event.get("event_time"), source="webhook"),
                "FULFIL_ORDER", f"order:{oid}:fulfil")
        elif b and paise(num(pay.get("payment_amount"))) and paise(num(pay.get("payment_amount"))) != paise(b["amount"]):
            amt = num(pay.get("payment_amount")); dlt = round(abs(amt - b["amount"]), 2)
            add("AMOUNT_MISMATCH", oid, dlt, "high" if amt < b["amount"] else "medium",
                f"Real time: books record {inr(b['amount'])} for {oid} but Cashfree captured {inr(amt)}.",
                dict(books_amount=b["amount"], captured=amt, anchor_time=pay.get("payment_time"), source="webhook"),
                "COLLECT_DIFFERENCE" if amt < b["amount"] else "CORRECT_BOOKS_OR_REFUND", f"order:{oid}:collect")
    elif et == "REFUND_STATUS_WEBHOOK":
        rf = d.get("refund") or {}
        oid = out["order_id"] = rf.get("order_id") or oid
        if (rf.get("refund_status") or "").upper() == "SUCCESS" and refunds_by_order is not None:
            live = [x for x in refunds_by_order(oid) if (x.get("refund_status") or "").upper() in ("SUCCESS", "PENDING", "ONHOLD")]
            same = [x for x in live if paise(num(x.get("refund_amount"))) == paise(num(rf.get("refund_amount")))]
            if len(same) > 1:
                add("DUPLICATE_REFUND", oid, num(rf.get("refund_amount")) * (len(same) - 1), "high",
                    f"Real time: {len(same)} refunds of {inr(num(rf.get('refund_amount')))} on {oid}: likely refunded twice.",
                    dict(refund_ids=[x.get("refund_id") for x in same], anchor_time=rf.get("processed_at") or event.get("event_time"), source="webhook"),
                    "CANCEL_OR_RECOVER_REFUND", f"order:{oid}:refund")
            if b and sum(paise(num(x.get("refund_amount"))) for x in live) > paise(b["amount"]):
                tot = sum(num(x.get("refund_amount")) for x in live)
                add("OVER_REFUND", oid, round(tot - b["amount"], 2), "critical", f"Real time: refunds {inr(tot)} exceed the sale {inr(b['amount'])} on {oid}.",
                    dict(anchor_time=event.get("event_time"), source="webhook"), "CANCEL_OR_RECOVER_REFUND", f"order:{oid}:refund")
    elif et.startswith("SETTLEMENT_"):
        s = d.get("settlement") or {}
        utr = s.get("utr") or f"UTR{s.get('cf_settlement_id') or s.get('settlement_id')}"
        amt = num(s.get("amount_settled") or s.get("settlement_amount"))
        out["order_id"] = None; out["settlement"] = dict(utr=utr, amount=amt, status=s.get("status"))
        if et in ("SETTLEMENT_FAILED", "SETTLEMENT_REVERSED"):
            add("SETTLEMENT_" + et.split("_")[1], utr, amt, "critical",
                f"Real time: Cashfree reports settlement {utr} of {inr(amt)} {et.split('_')[1].lower()}. That money will not "
                f"(or no longer) sit in your bank: {s.get('reason') or s.get('remarks') or 'no reason given'}.",
                dict(settlement=s, anchor_time=event.get("event_time"), source="webhook"), "RAISE_WITH_CASHFREE", f"settlement:{utr}")
        out["trigger_recon"] = et in ("SETTLEMENT_SUCCESS", "SETTLEMENT_REVERSED", "SETTLEMENT_FAILED")
    elif et.startswith("DISPUTE_"):
        dp = d.get("dispute") or {}
        amt = num(dp.get("dispute_amount"))
        if et == "DISPUTE_CREATED" and b and b.get("ledger_status") == "PAID":
            add("CHARGEBACK_RISK", oid, amt, "high",
                f"Real time: a {dp.get('dispute_type') or 'dispute'} of {inr(amt)} was raised on {oid}. If it is lost, Cashfree deducts it from a "
                "settlement while your books still count the sale. Respond before the deadline on the dispute.",
                dict(dispute=dp, anchor_time=event.get("event_time"), source="webhook"), "CONTEST_OR_ACCEPT_DISPUTE", f"order:{oid}:chargeback")
    return out
