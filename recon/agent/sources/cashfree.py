"""Read-mostly access to Cashfree for the agents (wraps integrations/cashfree/cf.py). Writes are isolated in `actions`."""
import os
import sys
import time
from functools import lru_cache

from ..config import ROOT

sys.path.insert(0, os.path.join(ROOT, "integrations", "cashfree"))
import cf  # noqa: E402

CashfreeError = cf.CashfreeError


def _try(fn, *a, default=None):
    try:
        return fn(*a)
    except cf.CashfreeError as e:
        if default is not None:
            return default
        return {"_error": e.status, "_body": e.body}


@lru_cache(maxsize=None)
def order_snapshot(order_id):
    """Everything an agent needs about one order: order, payments (oldest first), settlement, refunds."""
    order = _try(cf.get_order, order_id)
    pays = _try(cf.get_order_payments, order_id, default=[])
    pays = sorted(pays if isinstance(pays, list) else [], key=lambda p: p.get("payment_time") or "")
    settlement = None
    if any(p.get("payment_status") == "SUCCESS" for p in pays):
        s = _try(cf.get_order_settlements, order_id)
        settlement = None if "_error" in s else s
    refunds = _try(cf.get_refunds, order_id, default=[])
    return dict(order=order, payments=pays, settlement=settlement, refunds=refunds if isinstance(refunds, list) else [])


def gateway_status(snap):
    pays = snap["payments"]
    if any(p["payment_status"] == "SUCCESS" for p in pays):
        return "SUCCESS"
    return pays[-1]["payment_status"] if pays else "NO_PAYMENT"


def _window(days):
    return {"start_date": time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(time.time() - 86400 * days)),
            "end_date": time.strftime("%Y-%m-%dT23:59:59Z", time.gmtime(time.time() + 86400))}


def _paged(path, days, limit=50, max_pages=20):
    rows, cursor = [], None
    for _ in range(max_pages):
        body = {"pagination": {"limit": limit, **({"cursor": cursor} if cursor else {})}, "filters": _window(days)}
        r = cf.call("POST", path, body)
        rows += r.get("data") or []
        cursor = r.get("cursor")
        if not cursor:
            break
    return rows


@lru_cache(maxsize=None)
def settlement_batches(days=7):
    """One row per settlement batch: id, utr, amount_settled, charges, window."""
    out = []
    for r in _paged("/pg/settlements", days):
        s = r.get("settlement_details") or {}
        if s.get("cf_settlement_id"):
            out.append(s)
    return out


@lru_cache(maxsize=None)
def recon_feed(days=7):
    return _paged("/pg/settlement/recon", days)


@lru_cache(maxsize=None)
def transfer(transfer_id):
    return _try(cf.get_transfer, transfer_id)


@lru_cache(maxsize=None)
def beneficiary(beneficiary_id):
    return _try(cf.get_beneficiary, beneficiary_id)


@lru_cache(maxsize=None)
def upi_name_at_bank(vpa, claimed_name):
    """Secure ID UPI penny-drop: returns the account holder name the bank has for this VPA."""
    body = {"verification_id": cf._id("upi"), "vpa": vpa, "name": claimed_name,
            "user_consent": {"obtained": True, "type": "EXPLICIT", "purpose": "Payout beneficiary verification",
                             "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}
    r = _try(lambda: cf.call("POST", "/verification/upi/penny-drop", body))
    return r


def payment_link(link_id):
    return _try(cf.get_payment_link, link_id)


def payout_balance():
    return cf.get_balance().get("data")


def clear_cache():
    for fn in (order_snapshot, settlement_batches, recon_feed, transfer, beneficiary, upi_name_at_bank):
        fn.cache_clear()
