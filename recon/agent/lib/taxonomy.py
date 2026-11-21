"""Payment failure taxonomy (our build - no OSS exists).

Classifies a failure (gateway/NPCI code and/or free-text reason) into a recovery class:
  RETRY_LATER        transient: bank/PSP/biller downtime, network, technical error
  CHECK_STATUS       outcome unknown: timeout, pending, deemed, on hold -> reconcile status BEFORE retrying
                     (retrying blindly risks a double debit)
  RETRY_NEXT_DAY     limit / velocity caps reset with time
  ASK_CUSTOMER       customer must act (wrong PIN, insufficient funds, declined, expired, wrong account)
  NEVER_RETRY        permanent: invalid/closed/blocked account, invalid VPA, fraud/risk, not enabled, nothing due

Written from NPCI/card-network semantics BEFORE looking at the labelled evaluation sets.
"""
import re

RETRIABLE = {"RETRY_LATER", "CHECK_STATUS", "RETRY_NEXT_DAY"}

# NPCI UPI response codes (subset, from NPCI semantics as published by PSPs)
NPCI = {
    "U30": "ASK_CUSTOMER",   # debit failed at remitter bank
    "Z9": "ASK_CUSTOMER",    # insufficient funds
    "ZM": "ASK_CUSTOMER",    # invalid UPI PIN
    "Z8": "RETRY_NEXT_DAY",  # per-txn limit exceeded
    "Z7": "RETRY_NEXT_DAY",  # frequency limit exceeded
    "U16": "RETRY_NEXT_DAY", # risk threshold / limit exceeded
    "XY": "RETRY_LATER",     # remitter CBS offline / technical
    "U28": "RETRY_LATER", "U26": "RETRY_LATER", "U29": "RETRY_LATER",
    "U13": "CHECK_STATUS",   # external error / ack not received
    "U67": "CHECK_STATUS",   # debit timeout
    "BT": "CHECK_STATUS", "RB": "CHECK_STATUS",  # deemed
    "U69": "ASK_CUSTOMER",   # collect expired
    "ZA": "ASK_CUSTOMER",    # declined by customer
    "U66": "ASK_CUSTOMER",   # device fingerprint mismatch
    "ZH": "NEVER_RETRY",     # invalid VPA
    "B1": "NEVER_RETRY",     # registered mobile changed / blocked
    "YE": "NEVER_RETRY",     # account blocked/frozen
    "Z6": "ASK_CUSTOMER",    # PIN tries exceeded
}

# Cashfree PG error_details.error_code values seen live in sandbox (2026-11-20)
CASHFREE = {
    "TRANSACTION_DECLINED": "ASK_CUSTOMER",   # error_reason decline_originating_from_user, error_source customer
    "CUSTOMER_AUTH_FAILURE": "ASK_CUSTOMER",  # insufficient_funds / wrong PIN family
    "NETWORK_ERROR": "RETRY_LATER",           # issuer_bank_offline
    "ISSUER_NOT_AVAILABLE": "RETRY_LATER",
    "USER_DROPPED": "ASK_CUSTOMER",
    "CUSTOMER_CANCELLED": "ASK_CUSTOMER",     # request_expired (UPI collect not approved in time)
}

# ordered: first match wins. Permanent beats transient beats customer-action.
TEXT_RULES = [
    ("NEVER_RETRY", r"invalid (upi|vpa|account|customer|beneficiary|card)|beneficiary not found|account (is )?(blocked|closed|frozen|dormant)|"
                    r"not (activated|enabled|allowed|permitted|supported)|does not exist|no (bill|amount) (data|due)|no bill due|already paid|"
                    r"payment received for|fraud|stolen|lost card|restricted|blacklist|invalid combination|incorrect or invalid|"
                    r"incomplete details|kyc|duplicate|repeat (payment )?request|maximum .*count reached|expired card|"
                    r"not found|not registered|deactivated|invalid amount|not matching|reversal .*fail"),
    # v2: CHECK_STATUS must precede limits ("exceeded the time limit" is a timeout, not a daily cap)
    ("CHECK_STATUS", r"time ?out|timed out|time limit|pending|deemed|on hold|in progress|awaiting|ack not received|response not received|unknown status"),
    ("RETRY_NEXT_DAY", r"(daily|per day|transaction|txn|amount|frequency|count) limit|limit (reached|exceeded)|for the day|frequency|velocity|daily cap|too many attempts"),
    ("RETRY_LATER", r"downtime|technical|network|connectivity|server|unavailable|not reachable|unable to (get|connect|process|fetch)|"
                    r"try again|temporar|system (error|issue)|cbs|offline|service (error|down)|internal error|gateway error|biller.*(down|error)"),
    ("ASK_CUSTOMER", r"insufficient|mpin|upi pin|wrong pin|incorrect pin|declined|rejected by (customer|user|payer)|cancel|expired|"
                     r"mismatch|authentication|otp|3ds|do not honou?r|abandon|amount .*(less|more|mismatch)|partial"),
]
_COMPILED = [(c, re.compile(p, re.I)) for c, p in TEXT_RULES]


def classify(code=None, reason="", source=None):
    """code: NPCI or Cashfree error_code; source: Cashfree error_details.error_source (customer / bank / ...)."""
    c = (code or "").upper()
    if c in NPCI:
        return NPCI[c], f"npci:{c}"
    if c in CASHFREE:
        return CASHFREE[c], f"cashfree:{c}"
    for cls, rx in _COMPILED:
        m = rx.search(reason or "")
        if m:
            # v3 (live data): "Issue originating from user. Request user to try again" matched 'try again'.
            # A customer-sourced failure is never a transient bank glitch.
            if cls == "RETRY_LATER" and (source or "").lower() == "customer":
                return "ASK_CUSTOMER", f"text:{m.group(0).lower()}+source:customer"
            return cls, f"text:{m.group(0).lower()}"
    # v2: unknown codes go to a human, never to an automatic retry (v1 defaulted to RETRY_LATER: 9/14 BBPS errors)
    return "UNKNOWN", "default:unknown->human review"
