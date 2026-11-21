"""Operations view: the trace table turned into a live activity feed and per-agent success rates."""
from __future__ import annotations

import json
from datetime import timedelta

from . import daemon, recon
from .context import Ctx
from .db import iso, now
from .money import format_inr

FAILED = {"denied", "error", "provider_error"}   # "not_found" is a correct answer, not a failure
BAD_KINDS = {"denial", "webhook_rejected", "action_failed"}


def agent_of(r: dict) -> str:
    k, a, tgt = r["kind"], r["actor"] or "", r["target"] or ""
    if k == "handoff":
        return {"evidence": "Evidence agent", "reconciliation": "Reconciliation agent",
                "outreach": "Recovery agent"}.get(tgt, tgt)
    if k == "customer_message":
        return "Customer query agent"
    if k in ("utterance", "extraction"):
        return "Orchestrator"
    if k == "agent_run":
        return "Reconciliation engine"
    if k.startswith(("daemon", "follow_up")):
        return "Recovery daemon"
    if k.startswith("webhook") or a == "cashfree_webhook":
        return "Cashfree webhook"
    if k.startswith(("approval", "action_")):
        return "Approvals"
    if k == "queue_change":
        return "Priority engine"
    if k == "import":
        return "Importer"
    return "Guard" if k == "denial" else k


def _ok(r: dict, res: dict) -> bool:
    if r["kind"] in BAD_KINDS:
        return False
    return str(res.get("status", "ok")).split(":")[0] not in FAILED and res.get("ok", True) is not False


def text_of(r: dict, res: dict, inp: dict) -> str:
    k = r["kind"]
    if k == "handoff":
        return res.get("summary") or f"{r['allowed_action']}"
    if k == "customer_message":
        said = (inp.get("transcript") or "").replace("\n", " ")
        return f"{inp.get('via', 'text')}: “{said[:70]}{'…' if len(said) > 70 else ''}” → replied ({res.get('reply_source')})"
    if k == "daemon_prepared":
        return f"Prepared {res.get('name')} · {res.get('amount')}: {res.get('strategy')} Awaiting approval." \
            if res.get("status") == "needs_input" else f"Skipped {res.get('ref')}: {res.get('status')}"
    if k == "daemon_tick":
        return f"Pass: {res.get('eligible')} eligible of {res.get('ranked')}; prepared {', '.join(res.get('prepared') or []) or 'none'}"
    if k == "follow_up_scheduled":
        return f"Follow-up for {res.get('ref')} scheduled at {str(res.get('due_at', ''))[:16].replace('T', ' ')} UTC"
    if k == "follow_up_due":
        return f"Follow-up due for {res.get('ref')}: {res.get('unpaid')} still unpaid"
    if k == "agent_run":
        return (f"{res.get('findings')} findings, {format_inr(round((res.get('at_risk') or 0) * 100))} at stake ({res.get('mode')})"
                if res.get("ok") else f"failed: {res.get('error')}")
    if k == "utterance":
        return f"Operator said: “{(inp.get('text') or '')[:70]}”"
    if k == "extraction":
        return f"Intent {((res.get('extraction') or {}).get('intent'))} via {r['actor']}"
    if k == "queue_change":
        return f"{res.get('external_ref', '')} moved {res.get('from')} → {res.get('to')}".strip()
    if k.startswith("approval") or k.startswith("action_"):
        return f"{k.replace('_', ' ')} · {format_inr(res['amount_paise'])}" if res.get("amount_paise") else k.replace("_", " ")
    if k.startswith("webhook"):
        return f"{k.replace('_', ' ')}: {res.get('reason') or res.get('type') or ''}"
    return res.get("reason") or res.get("summary") or k.replace("_", " ")


def _noise(r: dict, res: dict) -> bool:
    # the reconciler re-reads open links every interval; only show it when something changed
    return r["actor"] == "reconciler" and r["allowed_action"] == "refresh_link" and res.get("status") == "ok" \
        and "paid" in (res.get("summary") or "") and "₹0 of" in (res.get("summary") or "")


def feed(ctx: Ctx, merchant_id: str, after_id: int = 0, limit: int = 40) -> tuple[list[dict], int]:
    rows = ctx.db.all("SELECT t.*, a.external_ref FROM trace t LEFT JOIN accounts a ON a.id=t.account_id "
                      "WHERE t.merchant_id=? AND t.id>? ORDER BY t.id DESC LIMIT ?", (merchant_id, after_id, limit * 3))
    top = max([after_id] + [r["id"] for r in rows])
    out = []
    for r in reversed(rows):
        res, inp = json.loads(r["result_json"] or "{}"), json.loads(r["input_json"] or "{}")
        if not isinstance(res, dict) or _noise(r, res):
            continue
        out.append({"id": r["id"], "ts": r["ts"], "kind": r["kind"], "agent": agent_of(r), "ref": r["external_ref"],
                    "ok": _ok(r, res), "text": text_of(r, res, inp if isinstance(inp, dict) else {})})
    return out[-limit:], top


def summary(ctx: Ctx, merchant_id: str) -> dict:
    db = ctx.db
    since = iso(now() - timedelta(hours=24))
    stats: dict[str, dict] = {}
    for r in db.all("SELECT kind, actor, target, allowed_action, result_json FROM trace WHERE merchant_id=? AND ts>=?",
                    (merchant_id, since)):
        res = json.loads(r["result_json"] or "{}")
        res = res if isinstance(res, dict) else {}
        s = stats.setdefault(agent_of(r), {"agent": agent_of(r), "runs": 0, "ok": 0, "errors": 0})
        s["runs"] += 1
        if _ok(r, res):
            s["ok"] += 1
        else:
            s["errors"] += 1
    runs = sum(s["runs"] for s in stats.values())
    ok = sum(s["ok"] for s in stats.values())
    received = db.one("SELECT COALESCE(SUM(amount_paise),0) s FROM receipts WHERE merchant_id=?", (merchant_id,))["s"]
    last_recon = recon.LAST_RUN.get(merchant_id)
    if not last_recon:  # after a restart: the last logged engine run
        row = db.one("SELECT result_json FROM trace WHERE merchant_id=? AND kind='agent_run' AND result_json LIKE '%\"ok\": true%' "
                     "ORDER BY id DESC LIMIT 1", (merchant_id,))
        last_recon = json.loads(row["result_json"]) if row else None
    items, _ = feed(ctx, merchant_id, 0, 40)
    return {
        "recovered_paise": received,
        "leakage_paise": round(last_recon["at_risk"] * 100) if last_recon else None,
        "leakage_findings": last_recon["findings"] if last_recon else None,
        "success_rate": round(ok / runs, 3) if runs else None, "runs_24h": runs,
        "agents": sorted(stats.values(), key=lambda s: -s["runs"]),
        "daemon": daemon.LAST.get(merchant_id), "activity": items,
    }
