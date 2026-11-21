"""Recovery daemon: runs on its own, independent of the dashboard.

Each tick, per merchant: rank the book, pick the next eligible customers, write a short repayment strategy,
and prepare a payment link for them. Preparing means an approval request: an operator still approves on screen
before any link is created or sent (voice and background jobs cannot authorize money actions). Unpaid links get
a follow-up scheduled by the contact policy. Payments are verified by the reconciler and Cashfree webhooks.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from .context import Ctx
from .money import format_inr
from .queue import build_queue
from .specialists import handoff
from .db import iso, now

log = logging.getLogger("daemon")
ACTOR = "recovery_daemon"
PREPARE_PER_TICK = 3          # at most this many new approval requests per merchant per tick
REPROPOSE_AFTER_HOURS = 24    # do not re-prepare a customer who had a request in the last day
FOLLOW_UP_AFTER_HOURS = 24    # an unpaid link gets a follow-up this long after it was sent
LAST: dict[str, dict] = {}    # merchant_id -> last tick summary (for the dashboard)

LANG_NAME = {"hi-IN": "Hindi", "ta-IN": "Tamil", "kn-IN": "Kannada", "en-IN": "English"}


def strategy(r: dict) -> str:
    """A short, plain repayment strategy from verified facts only."""
    lang = LANG_NAME.get(r.get("language") or "", "English")
    amt = format_inr(r["collectible_paise"])
    if r["collectible_paise"] < r["outstanding_paise"]:
        return f"Ask for the verified {amt} now (part of {format_inr(r['outstanding_paise'])}); message in {lang}."
    return f"Send a {lang} message with a payment link for {amt}; {r['reason'].rstrip('.')}."


def _recent(ctx: Ctx, kind: str, target: str, hours: int) -> bool:
    since = iso(now() - timedelta(hours=hours))
    return bool(ctx.db.one("SELECT 1 FROM trace WHERE kind=? AND target=? AND ts>=?", (kind, target, since)))


def tick(ctx: Ctx, merchant_id: str) -> dict:
    db = ctx.db
    q = build_queue(ctx, merchant_id, record_changes=False)
    rows = q["rows"]
    eligible = [r for r in rows if r["decision"] == "CONTACT"]
    prepared, follow_ups, waiting = [], [], []
    since = iso(now() - timedelta(hours=REPROPOSE_AFTER_HOURS))
    for r in eligible:
        link = r.get("link")
        if link and link["status"] in ("ACTIVE", "PARTIALLY_PAID"):
            sent = db.one("SELECT created_at FROM payment_links WHERE link_id=?", (link["link_id"],))["created_at"]
            due = datetime.fromisoformat(sent) + timedelta(hours=FOLLOW_UP_AFTER_HOURS)
            if not _recent(ctx, "follow_up_scheduled", link["link_id"], 24 * 30):
                db.trace(merchant_id, "follow_up_scheduled", ACTOR, account_id=r["account_id"], target=link["link_id"],
                         result={"ref": r["external_ref"], "due_at": iso(due), "policy": f"{FOLLOW_UP_AFTER_HOURS}h after link"})
            if now() >= due and r["contact_allowed_now"] and not _recent(ctx, "follow_up_due", link["link_id"], 24):
                db.trace(merchant_id, "follow_up_due", ACTOR, account_id=r["account_id"], target=link["link_id"],
                         result={"ref": r["external_ref"], "unpaid": format_inr(link["amount_paise"] - link["amount_paid_paise"])})
                follow_ups.append(r["external_ref"])
            continue
        if r.get("pending_approval") or len(prepared) >= PREPARE_PER_TICK:
            continue
        if not r["contact_allowed_now"]:
            waiting.append(r["external_ref"])
            continue
        if db.one("SELECT 1 FROM approvals WHERE account_id=? AND created_at>=?", (r["account_id"], since)):
            continue
        plan = strategy(r)
        try:
            status = handoff(ctx, merchant_id, r["account_id"], "outreach", "propose_payment_link", actor=ACTOR,
                             requested_by=ACTOR, requested_via="daemon").status
        except Exception as e:  # e.g. recipient not on the test list: recorded, and the loop moves on
            status = f"refused: {e}"
        db.trace(merchant_id, "daemon_prepared", ACTOR, account_id=r["account_id"], target=r["external_ref"],
                 result={"ref": r["external_ref"], "name": r["display_name"], "strategy": plan, "status": status,
                         "amount": format_inr(r["collectible_paise"]), "score": r["score"]})
        if status == "needs_input":
            prepared.append(r["external_ref"])
    nxt = next((r for r in eligible if not r.get("pending_approval") and not r.get("link")), None)
    summary = {"at": iso(), "ranked": len(rows), "eligible": len(eligible), "prepared": prepared,
               "follow_ups_due": follow_ups, "waiting_for_contact_hours": waiting,
               "next": {"ref": nxt["external_ref"], "name": nxt["display_name"]} if nxt else None}
    if prepared or follow_ups:
        db.trace(merchant_id, "daemon_tick", ACTOR, result=summary)
    LAST[merchant_id] = summary
    return summary


def tick_all(ctx: Ctx) -> None:
    for m in ctx.db.all("SELECT DISTINCT merchant_id FROM accounts"):
        try:
            tick(ctx, m["merchant_id"])
        except Exception:
            log.exception("daemon tick failed for %s", m["merchant_id"])


class RecoveryDaemon(threading.Thread):
    def __init__(self, ctx: Ctx, interval: int):
        super().__init__(daemon=True, name="recovery-daemon")
        self.ctx, self.interval = ctx, interval
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.wait(self.interval):
            tick_all(self.ctx)
