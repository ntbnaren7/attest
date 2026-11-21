"""Deadlines for findings. Each carries its basis so the merchant knows whether it is law or policy.

Only the RBI turnaround time is regulatory (RBI circular DPSS.CO.PD No.629/02.01.014/2019-20, 2019-09-20):
a customer debited for a merchant payment the merchant never confirmed must be auto-reversed by T+5, with
₹100/day compensation after that. Every other deadline is merchant policy from config.py.
"""
import re
from datetime import datetime, timedelta

from ..config import (COLLECTION_DAYS, DISPUTE_RESPONSE_DAYS, ESCALATION_DAYS, FULFIL_DAYS, RBI_MERCHANT_TAT_DAYS,
                      SETTLEMENT_SLA_HOURS)
from .dates import IST

_ESC = (timedelta(days=ESCALATION_DAYS),
        f"merchant policy: raise within {ESCALATION_DAYS} days (ESCALATION_DAYS; set it to your Cashfree/bank dispute window)")
_COLLECT = (timedelta(days=COLLECTION_DAYS), f"merchant policy: collect within {COLLECTION_DAYS} days of the sale (COLLECTION_DAYS)")
RBI = (timedelta(days=RBI_MERCHANT_TAT_DAYS),
       "RBI TAT (DPSS.CO.PD No.629/02.01.014/2019-20): customer debited but merchant not confirmed must be reversed "
       f"by T+{RBI_MERCHANT_TAT_DAYS}; ₹100/day compensation after that. Confirm the status before chasing")

RULES = {
    "CAPTURED_NOT_SETTLED": (timedelta(hours=SETTLEMENT_SLA_HOURS),
                             f"contracted settlement cycle: {SETTLEMENT_SLA_HOURS}h after capture (SETTLEMENT_SLA_HOURS)"),
    "MISSING_CREDIT": _ESC, "SHORT_CREDIT": _ESC, "EXCESS_CREDIT": _ESC, "DUPLICATE_CREDIT": _ESC,
    "BATCH_TOTAL_MISMATCH": _ESC,
    "UNEXPLAINED_CASHFREE_CREDIT": (_ESC[0], _ESC[1] + ". Keep it in suspense (don't spend it) until the bank confirms"),
    "FULFILLED_BUT_UNPAID": _COLLECT, "RECOVER": _COLLECT, "AMOUNT_MISMATCH": _COLLECT,
    "PAID_NOT_FULFILLED": (timedelta(days=FULFIL_DAYS), f"customer has paid: fulfil or refund within {FULFIL_DAYS} days (FULFIL_DAYS)"),
    "FEE_OVERCHARGE": _ESC, "GST_MISMATCH": _ESC, "NET_SETTLEMENT_SHORT": _ESC,
    "DUPLICATE_REFUND": _ESC, "OVER_REFUND": _ESC, "DUPLICATE_PAYOUT": _ESC, "PAYOUT_OVER_LIMIT_NO_APPROVAL": _ESC,
    "TIEOUT_BREAK": (timedelta(0), "books cannot be signed off until this is explained"),
    "CREDIT_REVERSED": _ESC, "NEEDS_REVIEW": _ESC, "SETTLEMENT_FAILED": _ESC, "SETTLEMENT_REVERSED": _ESC,
    "CHARGEBACK_RISK": (timedelta(days=DISPUTE_RESPONSE_DAYS), f"merchant policy: respond within {DISPUTE_RESPONSE_DAYS} days; the binding date is the respond-by date on the Cashfree dispute"), "UNMATCHED_REVERSAL": _ESC, "REFUND_NOT_IN_BOOKS": _ESC, "ADJUSTMENT_DEDUCTED": _ESC,
    "DUPLICATE_PAYMENT": (timedelta(days=FULFIL_DAYS), f"merchant policy: confirm with the customer and refund the extra payment within {FULFIL_DAYS} days"),
    "CHARGEBACK_DEDUCTED": (timedelta(days=DISPUTE_RESPONSE_DAYS),
                            f"merchant policy: respond within {DISPUTE_RESPONSE_DAYS} days (DISPUTE_RESPONSE_DAYS); the binding date is the respond-by date on the Cashfree dispute"),
    "UNVERIFIED_DIRECT_PAYMENT": (timedelta(0), "immediately: do not release goods; if a screenshot was shown, report on 1930 / cybercrime.gov.in"),
    "DIRECT_PAYMENT_SHORT": (timedelta(0), "immediately: the amount received is less than recorded; report on 1930 / cybercrime.gov.in if a screenshot was shown"),
}
OUTCOME_UNKNOWN = {"PENDING", "NOT_ATTEMPTED", "FLAGGED"}
# failure texts that cannot rule out that the customer was debited (timeouts, holds, server-side faults: the 12 Apr 2025
# UPI outage left debits pending exactly this way). For shipped goods these get the RBI TAT, not merchant policy.
_UNKNOWN_TEXT = re.compile(r"time ?out|timed out|time limit|pending|deemed|on hold|ack not received|response not received|"
                           r"unknown status|server|technical", re.I)


def outcome_unknown(evidence):
    """One rule used by every agent, so the same money never gets two different deadlines."""
    if evidence.get("gateway_status") in OUTCOME_UNKNOWN or evidence.get("failure_class") == "CHECK_STATUS":
        return True
    err = evidence.get("error") or {}
    text = " ".join(str(err.get(k) or "") for k in ("error_description", "error_reason", "error_code")) + " " + str(evidence.get("error_description") or "")
    return bool(_UNKNOWN_TEXT.search(text))


def _aware(t):
    if t is None:
        return None
    if isinstance(t, str):
        try:
            t = datetime.fromisoformat(t)
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=IST)


def deadline(kind, evidence, now):
    rule = RULES.get(kind)
    if not rule:
        return None
    delta, basis = rule
    if kind in ("FULFILLED_BUT_UNPAID", "RECOVER") and outcome_unknown(evidence):
        delta, basis = RBI
    anchor = _aware(evidence.get("anchor_time")) or (now if kind == "TIEOUT_BREAK" else None)
    if anchor is None:
        return None
    due = anchor + delta
    left = (due - now).total_seconds() / 86400
    return dict(due=due.isoformat(timespec="minutes"), days_left=round(left, 1), overdue=left < 0, basis=basis)
