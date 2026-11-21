"""Recovery: collect money that reconciliation proved is lost.

Scope is deliberately narrow. Cashfree Relay already chases ordinary failed or abandoned payments (its Payment
Recovery, Cart Recovery and Subscription Dunning agents). This agent only handles orders the merchant's books mark
PAID and fulfilled while Cashfree never captured the money, i.e. the goods left and the money did not arrive.
It explains *why* the payment failed (taxonomy), picks a safe strategy (rules/recovery_strategy.json) and drafts
the collection action for human approval. It never retries blindly: unknown outcomes are checked first.
"""
from ..config import DIRECT_MODES, P_RECOVER
from ..lib.findings import Finding
from ..lib.money import inr
from ..lib.rules import decide
from ..lib.taxonomy import classify
from ..sources import cashfree as cfio


def recover_order(o, snap=None):
    """Recovery decision for ONE order dict(order_id, customer, amount). Returns a Finding, or None if paid."""
    snap = snap or cfio.order_snapshot(o["order_id"]); gw = cfio.gateway_status(snap)
    if gw == "SUCCESS":
        return None
    pays = snap["payments"]; last = pays[-1] if pays else {}
    err = last.get("error_details") or {}
    if gw == "FAILED":
        reason = " ".join(str(err.get(k, "")) for k in ("error_code", "error_reason", "error_description"))
        cls, why = classify(err.get("error_code"), reason, err.get("error_source"))
    elif gw == "USER_DROPPED":
        cls, why = classify("USER_DROPPED")
    elif gw == "NO_PAYMENT":
        cls, why = "ASK_CUSTOMER", "no payment attempt on record"
    else:
        cls, why = classify(None, f"payment {gw.lower()}")
    attempts = len(pays)
    d = decide("recovery_strategy.json", dict(cls=cls, amount=o["amount"], attempts=attempts))
    return Finding("recovery", "RECOVER", o["order_id"], o["amount"], "high" if o["amount"] >= 10_000 else "medium",
                   f"{o['customer']}: goods delivered but {inr(o['amount'])} {gw.lower()} ({err.get('error_code') or gw}). {d['why']}.",
                   dict(gateway_status=gw, error=err, failure_class=cls, classified_by=why, attempts=attempts,
                        order_status=(snap["order"] or {}).get("order_status"), customer=o["customer"],
                        anchor_time=(last.get("payment_time") or (snap["order"] or {}).get("created_at")),
                        error_description=(err.get("error_description") or "").strip()),
                   action=d["action"], needs_approval=bool(d.get("approval")), priority=round(o["amount"] * P_RECOVER.get(cls, 0.2), 2),
                   money_key=f"order:{o['order_id']}:collect")  # same rupees as recon FULFILLED_BUT_UNPAID


def run_recovery(ctx):
    out = []
    for o in ctx["ledger"]["orders"]:
        if o["ledger_status"] != "PAID":  # unpaid + not shipped = ordinary failed payment: Relay's job
            continue
        if o.get("mode") in DIRECT_MODES:  # paid outside Cashfree: recon verifies it against the bank (possible fraud)
            continue
        f = recover_order(o)
        if f:
            out.append(f)
    return out
