"""Revenue leakage: money lost through wrong fees/GST, short net settlements, duplicate or excess refunds,
duplicate payouts and payouts that bypassed approval. Money maths in paise with Cashfree-compatible rounding."""
from collections import defaultdict

from ..config import GST_RATE, MONEY_TOLERANCE, PAYOUT_AUTO_APPROVE_LIMIT, PG_FEE_RATE
from ..lib.findings import Finding
from ..lib.money import cf_round, inr, paise, same
from ..sources import cashfree as cfio

LIVE_REFUND = ("SUCCESS", "PENDING", "ONHOLD")


def _fees(L, out):
    rows = []
    for o in L["orders"]:
        s = cfio.order_snapshot(o["order_id"])["settlement"]
        if s and s.get("service_charge") is not None:
            rows.append(dict(order_id=o["order_id"], amount=s["payment_amount"], fee=s["service_charge"], gst=s["service_tax"],
                             net=s["settlement_amount"], anchor_time=s.get("payment_time")))
    rate = PG_FEE_RATE["default"]
    for r in rows:
        exp_fee = cf_round(r["amount"] * rate); exp_gst = cf_round(r["fee"] * GST_RATE)
        if not same(r["fee"], exp_fee, MONEY_TOLERANCE):
            out.append(Finding("leakage", "FEE_OVERCHARGE", r["order_id"], cf_round(r["fee"] - exp_fee), "high",
                               f"Fee {inr(r['fee'])} on {inr(r['amount'])} vs contracted {rate * 100:.2f}% = {inr(exp_fee)}.",
                               dict(r, expected_fee=exp_fee), action="RAISE_WITH_CASHFREE", priority=r["fee"] - exp_fee,
                               money_key=f"order:{r['order_id']}:fee"))
        if not same(r["gst"], exp_gst, MONEY_TOLERANCE):
            out.append(Finding("leakage", "GST_MISMATCH", r["order_id"], cf_round(r["gst"] - exp_gst), "medium",
                               f"GST {inr(r['gst'])} on fee {inr(r['fee'])} vs 18% = {inr(exp_gst)}.", dict(r, expected_gst=exp_gst),
                               money_key=f"order:{r['order_id']}:gst"))
        if not same(r["net"], r["amount"] - r["fee"] - r["gst"], MONEY_TOLERANCE):
            out.append(Finding("leakage", "NET_SETTLEMENT_SHORT", r["order_id"], cf_round(r["amount"] - r["fee"] - r["gst"] - r["net"]), "high",
                               f"Net {inr(r['net'])} ≠ amount − fee − GST.", r, money_key=f"order:{r['order_id']}:net"))
    return len(rows)


def _refunds(L, out):
    """Duplicates (same order + amount) and refunds exceeding the payment."""
    by_order = defaultdict(list)
    for rf in L["refunds"]:
        by_order[rf["order_id"]].append(rf)
    for oid in by_order:
        live = cfio.order_snapshot(oid)["refunds"]
        amount = next(o["amount"] for o in L["orders"] if o["order_id"] == oid)
        groups = defaultdict(list)
        for x in live:
            if x.get("refund_status") in LIVE_REFUND:
                groups[paise(x["refund_amount"])].append(x)
        for xs in groups.values():
            if len(xs) > 1:
                extra = sum(float(x["refund_amount"]) for x in xs[1:])
                out.append(Finding("leakage", "DUPLICATE_REFUND", oid, extra, "high",
                                   f"{len(xs)} refunds of {inr(xs[0]['refund_amount'])} on the same order: {inr(extra)} likely refunded twice.",
                                   dict(refund_ids=[x["refund_id"] for x in xs], notes=[x.get("refund_note") for x in xs],
                                        created=[x.get("created_at") for x in xs], anchor_time=xs[-1].get("created_at")),
                                   action="CANCEL_OR_RECOVER_REFUND", needs_approval=False, priority=extra,  # ops task: no API to claw back
                                   money_key=f"order:{oid}:refund"))  # same rupees as OVER_REFUND
        total = sum(float(x["refund_amount"]) for x in live if x.get("refund_status") in LIVE_REFUND)
        if paise(total) > paise(amount):
            out.append(Finding("leakage", "OVER_REFUND", oid, cf_round(total - amount), "critical",
                               f"Refunds {inr(total)} exceed payment {inr(amount)}.",
                               dict(total=total, amount=amount, anchor_time=max((x.get("created_at") or "" for x in live), default=None)),
                               money_key=f"order:{oid}:refund"))


def _payouts(L, out):
    """Duplicate payouts (same beneficiary+amount or same payee+invoice) and over-limit payouts with no approver."""
    ok = [dict(p, live=cfio.transfer(p["transfer_id"])) for p in L["payouts"]]
    ok = [p for p in ok if str(p["live"].get("status")) == "SUCCESS"]
    seen = defaultdict(list)
    for p in ok:
        seen[(p["beneficiary_id"], paise(p["amount"]))].append(p)
        seen[("invoice", p["payee"].upper(), p["invoice"])].append(p)
    flagged = set()
    for ps in seen.values():
        if len(ps) > 1 and not flagged & {x["transfer_id"] for x in ps[1:]}:
            for dup in ps[1:]:
                flagged.add(dup["transfer_id"])
                out.append(Finding("leakage", "DUPLICATE_PAYOUT", dup["transfer_id"], dup["amount"], "critical",
                                   f"{inr(dup['amount'])} paid twice to {dup['payee']} for {dup['invoice']} (both SUCCESS, UTRs "
                                   f"{ps[0]['live'].get('transfer_utr')} and {dup['live'].get('transfer_utr')}).",
                                   dict(first=ps[0]["transfer_id"], duplicate=dup["transfer_id"], invoice=dup["invoice"], anchor_time=dup["live"].get("added_on")),
                                   action="RECOVER_FROM_PAYEE", needs_approval=False, priority=dup["amount"],  # ops task: no API to claw back
                                   money_key=f"transfer:{dup['transfer_id']}"))
    for p in ok:
        if p["amount"] > PAYOUT_AUTO_APPROVE_LIMIT and not p.get("approved_by"):
            out.append(Finding("leakage", "PAYOUT_OVER_LIMIT_NO_APPROVAL", p["transfer_id"], p["amount"], "high",
                               f"{inr(p['amount'])} to {p['payee']} exceeded the {inr(PAYOUT_AUTO_APPROVE_LIMIT)} limit with no approver on record.",
                               dict(payee=p["payee"], invoice=p["invoice"], anchor_time=p["live"].get("added_on")), action="POST_FACTO_REVIEW",
                               priority=p["amount"] * 0.3, money_key=f"transfer:{p['transfer_id']}"))  # same rupees as a DUPLICATE_PAYOUT on it


def run_leakage(ctx):
    L, out = ctx["ledger"], []
    checked = _fees(L, out)
    _refunds(L, out)
    _payouts(L, out)
    ctx["leakage_stats"] = dict(payments_checked=checked)
    return out
