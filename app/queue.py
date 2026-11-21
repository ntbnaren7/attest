"""Queue assembly and the background reconciler that re-forms it as provider evidence arrives."""
from __future__ import annotations

import logging
import threading

from .context import Ctx
from .db import iso
from .money import format_inr
from .specialists import allocate_receipts, evaluate, handoff

log = logging.getLogger("receivables")
LAST_RECONCILE: dict = {}   # when the provider poll last ran and how many records it checked (for the live panel)
ORDER = {"CONTACT": 0, "HOLD": 1, "REVIEW": 2, "CLOSED": 3}


def build_queue(ctx: Ctx, merchant_id: str, batch_id: str | None = None, record_changes: bool = True) -> dict:
    db = ctx.db
    sql = "SELECT * FROM accounts WHERE merchant_id=?"
    params: tuple = (merchant_id,)
    if batch_id:
        sql += " AND batch_id=?"
        params += (batch_id,)
    rows = []
    for a in db.all(sql, params):
        d = evaluate(ctx, merchant_id, a["id"])
        link = db.one("SELECT link_id, link_url, status, amount_paise, amount_paid_paise, provenance FROM payment_links "
                      "WHERE account_id=? ORDER BY created_at DESC", (a["id"],))
        pending = db.one("SELECT id, amount_paise, status FROM approvals WHERE account_id=? AND status IN ('pending','approved')"
                         " ORDER BY created_at DESC", (a["id"],))
        rows.append({"account_id": a["id"], "external_ref": a["external_ref"], "display_name": a["display_name"],
                     "language": a["language"], "version": a["version"], "batch_id": a["batch_id"], **d.as_dict(),
                     "outstanding_inr": format_inr(d.outstanding_paise), "collectible_inr": format_inr(d.collectible_paise),
                     "link": link, "pending_approval": pending})
    rows.sort(key=lambda r: (ORDER[r["decision"]], not r["contact_allowed_now"], -r["score"], -r["collectible_paise"],
                             r["external_ref"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    changes = []
    if record_changes:
        with db.tx():
            for r in rows:
                prev = db.one("SELECT * FROM queue_snapshot WHERE account_id=?", (r["account_id"],))
                if prev and (prev["decision"] != r["decision"] or prev["collectible_paise"] != r["collectible_paise"]):
                    ch = {"account_id": r["account_id"], "external_ref": r["external_ref"],
                          "from": prev["decision"], "to": r["decision"], "from_rank": prev["rank"], "to_rank": r["rank"],
                          "from_amount": format_inr(prev["collectible_paise"] or 0), "to_amount": r["collectible_inr"],
                          "why": r["reason"], "at": iso()}
                    changes.append(ch)
                    db.trace(merchant_id, "queue_change", "queue", account_id=r["account_id"], result=ch)
                db.x("INSERT INTO queue_snapshot (account_id, merchant_id, decision, rank, collectible_paise, reason, computed_at)"
                     " VALUES (?,?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET decision=excluded.decision,"
                     " rank=excluded.rank, collectible_paise=excluded.collectible_paise, reason=excluded.reason,"
                     " computed_at=excluded.computed_at",
                     (r["account_id"], merchant_id, r["decision"], r["rank"], r["collectible_paise"], r["reason"], iso()))
    counts = {k: sum(1 for r in rows if r["decision"] == k) for k in ORDER}
    ids = [r["account_id"] for r in rows]
    received = 0
    if ids:
        marks = ",".join("?" * len(ids))
        received = db.one(f"SELECT COALESCE(SUM(amount_paise),0) AS s FROM receipts WHERE merchant_id=? AND account_id IN ({marks})",
                          (merchant_id, *ids))["s"]
    totals = {"outstanding_paise": sum(r["outstanding_paise"] for r in rows),
              "collectible_now_paise": sum(r["collectible_paise"] for r in rows if r["decision"] == "CONTACT"),
              "received_paise": received,
              "on_hold_paise": sum(r["outstanding_paise"] for r in rows if r["decision"] in ("HOLD", "REVIEW"))}
    return {"rows": rows, "counts": counts, "totals": totals, "changes": changes, "computed_at": iso()}


def reconcile_once(ctx: Ctx) -> int:
    """Re-read every open link and every claim stuck on a provider error. Returns number of checks."""
    n = 0
    db = ctx.db
    for l in db.all("SELECT * FROM payment_links WHERE status IN ('ACTIVE','PARTIALLY_PAID')"):
        try:
            handoff(ctx, l["merchant_id"], l["account_id"], "evidence", "refresh_link", actor="reconciler", link_id=l["link_id"])
            allocate_receipts(ctx, l["merchant_id"], l["account_id"])
            n += 1
        except Exception:  # keep the loop alive; the failure is visible as provider_check_error
            log.exception("refresh_link failed")
    for a in db.all("SELECT DISTINCT merchant_id, account_id FROM claims WHERE kind='paid' AND status='provider_error'"):
        try:
            handoff(ctx, a["merchant_id"], a["account_id"], "evidence", "refresh_account", actor="reconciler")
            n += 1
        except Exception:
            log.exception("refresh_account failed")
    LAST_RECONCILE.update(at=iso(), checked=n)
    if n:
        for m in db.all("SELECT DISTINCT merchant_id FROM payment_links"):
            build_queue(ctx, m["merchant_id"])
    return n


class Reconciler(threading.Thread):
    def __init__(self, ctx: Ctx):
        super().__init__(daemon=True, name="reconciler")
        self.ctx = ctx
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.wait(self.ctx.settings.reconcile_interval_seconds):
            try:
                reconcile_once(self.ctx)
            except Exception:
                log.exception("reconcile loop error")
