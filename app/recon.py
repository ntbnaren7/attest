"""Adapter for the teammate's reconciliation & leakage engine (vendored in recon/).

The engine compares the merchant's books, Cashfree and the bank statement and returns findings
(unpaid shipped orders, unsettled captures, short or missing bank credits, duplicate refunds ...),
a tie-out that proves every rupee is accounted for, and a batch of actions that need approval.

Data source:
  recorded (default)  Cashfree responses recorded from the teammate's sandbox account for the shipped
                      scenario. Those orders exist only in that account, so live calls with our keys
                      would report them all as missing.
  live                set RECON_CF_PG_CLIENT_ID / RECON_CF_PG_CLIENT_SECRET (that account's PG test keys).

Approvals here are always a dry run: the engine shows exactly what it would send, nothing moves.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid

from .config import ROOT

RECON_DIR = ROOT / "recon"
RUN = "20261120_094955"
AGENTS = ["recon", "leakage", "recovery"]   # payouts belongs to Problem 3 and needs payout keys

_lock = threading.Lock()
_state: dict = {}
THREADS: dict[str, dict] = {}   # thread_id -> {merchant_id, pending, created}


def mode() -> str:
    return "live" if os.environ.get("RECON_CF_PG_CLIENT_ID") and os.environ.get("RECON_CF_PG_CLIENT_SECRET") else "recorded"


def _resolve(path: str):
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else str(ROOT / p)


def payouts_live() -> bool:
    """Payout reads need the keys and the 2FA public-key file to be present on this machine."""
    key = os.environ.get("RECON_CF_PUBLIC_KEY_PATH", "")
    return bool(os.environ.get("RECON_CF_PAYOUT_CLIENT_SECRET") and key and os.path.exists(_resolve(key)))


def _engine():
    """Import the engine once and point its Cashfree reads at the chosen source."""
    with _lock:
        if "app" in _state:
            return _state
        if str(RECON_DIR) not in sys.path:
            sys.path.insert(0, str(RECON_DIR))
        if mode() == "live":
            os.environ["CF_PG_CLIENT_ID"] = os.environ["RECON_CF_PG_CLIENT_ID"]
            os.environ["CF_PG_CLIENT_SECRET"] = os.environ["RECON_CF_PG_CLIENT_SECRET"]
        if payouts_live():
            # Payouts and Secure ID requests must be signed with the account's 2FA public key (.pem).
            for ours, theirs in (("RECON_CF_PAYOUT_CLIENT_ID", "CF_PAYOUT_CLIENT_ID"), ("RECON_CF_PAYOUT_CLIENT_SECRET", "CF_PAYOUT_CLIENT_SECRET"),
                                 ("RECON_CF_VERIFY_CLIENT_ID", "CF_VERIFY_CLIENT_ID"), ("RECON_CF_VERIFY_CLIENT_SECRET", "CF_VERIFY_CLIENT_SECRET"),
                                 ("RECON_CF_PUBLIC_KEY_PATH", "CF_PUBLIC_KEY_PATH"), ("RECON_CF_VERIFY_PUBLIC_KEY_PATH", "CF_VERIFY_PUBLIC_KEY_PATH")):
                if os.environ.get(ours):
                    os.environ[theirs] = str(_resolve(os.environ[ours]))
        from agent.sources import cashfree as cfio  # noqa: E402 (vendored package)

        original_try = cfio._try

        def _try(fn, *a, default=None):
            # A Cashfree product without keys here (e.g. Payouts) means "no data", not a crash.
            try:
                return original_try(fn, *a, default=default)
            except RuntimeError:
                return default if default is not None else {"_error": "not_configured"}

        cfio._try = _try
        if mode() == "recorded":
            snap = json.loads((RECON_DIR / "data" / "recorded_cashfree_snapshot.json").read_text())
            empty = dict(order=None, payments=[], settlement=None, refunds=[])
            cfio.order_snapshot = lambda oid: snap["snaps"].get(oid) or empty
            cfio.settlement_batches = lambda days=7: snap["batches"]
            cfio.recon_feed = lambda days=7: snap["feed"]

            def clear_cache():  # only the real cached lookups have caches; recorded ones have nothing to clear
                for name in ("order_snapshot", "settlement_batches", "recon_feed", "transfer", "beneficiary", "upi_name_at_bank"):
                    getattr(getattr(cfio, name), "cache_clear", lambda: None)()

            cfio.clear_cache = clear_cache
        from agent.lib.findings import Finding, exposure, ranked  # noqa: E402
        from agent.pipeline import graph  # noqa: E402
        from langgraph.types import Command  # noqa: E402

        _state.update(app=graph.build(), cfio=cfio, Finding=Finding, exposure=exposure, ranked=ranked, Command=Command)
        return _state


def available() -> tuple[bool, str]:
    try:
        _engine()
        return True, ""
    except Exception as e:  # missing dependency or files: the rest of the dashboard keeps working
        return False, f"{e.__class__.__name__}: {e}"


def _view(st: dict, thread_id: str) -> dict:
    E = _engine()
    fs = E["ranked"]([E["Finding"](**f) for f in st.get("findings", [])])
    at_risk, _ = E["exposure"](fs)
    pending = st["__interrupt__"][0].value["pending_approvals"] if st.get("__interrupt__") else THREADS[thread_id]["pending"]
    tie = (st.get("stats") or {}).get("tieout") or {}
    return dict(
        thread_id=thread_id, run=RUN, mode=mode(), brief=st.get("brief"),
        tieout=tie, findings=[f.to_dict() for f in fs], pending=pending, results=st.get("results", []),
        kpi=dict(findings=len(fs), critical=sum(f.severity == "critical" for f in fs),
                 high=sum(f.severity == "high" for f in fs), at_risk=round(at_risk, 2), approvals=len(pending)),
    )


LAST_RUN: dict[str, dict] = {}   # merchant_id -> last run's KPIs (for the operations view)


def run(merchant_id: str) -> dict:
    E = _engine()
    thread_id = f"recon_{uuid.uuid4().hex[:10]}"
    THREADS[thread_id] = dict(merchant_id=merchant_id, pending=[], created=time.time(), decided=False)
    E["cfio"].clear_cache()
    st = E["app"].invoke({"run": RUN, "agents": AGENTS, "execute": False, "findings": []},
                         {"configurable": {"thread_id": thread_id}})
    v = _view(st, thread_id)
    THREADS[thread_id]["pending"] = v["pending"]
    LAST_RUN[merchant_id] = dict(v["kpi"], mode=v["mode"])
    return v


def approve(merchant_id: str, thread_id: str, decisions: dict[str, bool]) -> dict:
    """Resume the paused batch once, as a dry run. Returns what would have been sent."""
    t = THREADS.get(thread_id)
    if not t or t["merchant_id"] != merchant_id:
        raise KeyError("unknown reconciliation run")
    if t["decided"]:
        raise ValueError("this batch was already decided")
    t["decided"] = True
    E = _engine()
    ids = {p["id"] for p in t["pending"]}
    clean = {k: bool(v) for k, v in decisions.items() if k in ids}
    st = E["app"].invoke(E["Command"](resume=clean, update={"execute": False}), {"configurable": {"thread_id": thread_id}})
    return _view(st, thread_id)
