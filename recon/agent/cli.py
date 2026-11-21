"""Run the orchestrator end to end.

    python -m agent.cli --run 20261120_094955                 # analyse, approve nothing, dry-run
    python -m agent.cli --run 20261120_094955 --approve ask   # approve each action interactively
    python -m agent.cli --run ... --approve all --execute     # approve all and really call Cashfree (sandbox)
    python -m agent.cli --run ... --agents recon,leakage       # subset

Writes runs/reports/agent_<ts>/{findings.json, REPORT.md}.
"""
import argparse
import json
import os
import time
import warnings

warnings.filterwarnings("ignore")
from langgraph.types import Command  # noqa: E402

from .pipeline import graph  # noqa: E402
from .config import REPORTS  # noqa: E402
from .eval import headline, score  # noqa: E402
from .lib.findings import Finding, ranked  # noqa: E402
from .lib.money import inr  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--agents", default="recon,leakage,recovery,payouts")
    ap.add_argument("--approve", default="none", help="none | all | ask | comma-separated ids e.g. payouts:REQ-07,recovery:<order_id>")
    ap.add_argument("--execute", action="store_true", help="really call Cashfree for approved actions (sandbox)")
    a = ap.parse_args()

    app = graph.build(); cfg = {"configurable": {"thread_id": f"run-{a.run}-{time.time()}"}}
    t0 = time.time()
    out = app.invoke({"run": a.run, "agents": a.agents.split(","), "execute": a.execute, "findings": []}, cfg)
    pending = out["__interrupt__"][0].value["pending_approvals"] if out.get("__interrupt__") else []
    print(f"\n{out['brief']}\n\n{len(pending)} actions need approval ({'EXECUTE' if a.execute else 'dry-run'}).")
    if a.approve == "ask":
        decision = {}
        for p in pending:
            ans = input(f"  approve {p['id']} {p['action']} {inr(p['amount'])}? {p['plan']} [y/N] ").strip().lower()
            decision[p["id"]] = ans == "y"
    elif a.approve in ("none", "all"):
        decision = a.approve
    else:
        decision = {i.strip(): True for i in a.approve.split(",")}
    out = app.invoke(Command(resume=decision), cfg)
    elapsed = time.time() - t0

    d = os.path.join(REPORTS, f"agent_{time.strftime('%Y%m%d_%H%M%S')}"); os.makedirs(d, exist_ok=True)
    F = dict(run=a.run, elapsed_s=round(elapsed, 1), stats=out.get("stats"), findings=out["findings"], results=out.get("results", []),
             brief=out["brief"], brief_source=out["brief_source"])
    json.dump(F, open(os.path.join(d, "findings.json"), "w"), indent=1, default=str)
    S = score(out["findings"], a.run, out.get("stats"))
    json.dump(S, open(os.path.join(d, "eval.json"), "w"), indent=1, default=str)
    fs = ranked([Finding(**f) for f in out["findings"]])
    md = [f"# Agent run on seed `{a.run}`\n", f"{len(fs)} findings in {elapsed:.0f}s. Mode: {'EXECUTE' if a.execute else 'dry-run'}, approvals: {a.approve}.\n",
          "## Brief\n", out["brief"], "\n## Score vs ground truth\n", "| Agent | Check | Found | FP | Precision | Recall |", "|---|---|---|---|---|---|"]
    md += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |" for r in headline(S)]
    T = (out.get("stats") or {}).get("tieout")
    if T:
        md += ["\n## Reconciliation tie-out\n", f"**{'Ties out: every rupee is explained by a finding.' if T['ok'] else 'BROKEN: unexplained money, do not sign off.'}**\n"]
        for sec in T["sections"]:
            status = "✅ residual ₹0.00" if sec["ok"] else "❌ residual " + inr(sec["residual"])
            md += [f"**{sec['title']}** ({status})\n",
                   "| Line | ₹ |", "|---|---|"] + [f"| {l['label']} | {inr(l['amount'])} |" for l in sec["lines"]]
            md += [f"\n_{sec['note']}_\n" if sec.get("note") else ""]
        if T.get("in_transit"):
            md += [f"In transit (not yet due): {', '.join(T['in_transit'])}\n"]
    ex = (out.get("stats") or {}).get("exposure")
    if ex:
        md += [f"\n**At stake: {inr(ex['at_stake'])}** across {ex['distinct_items']} distinct items ({ex['same_money_merged']} findings merged as the same money).\n"]
    md += ["\n## All findings\n", "| Sev | Agent | Kind | Entity | Amount | Counted | Due | Action | Summary |", "|---|---|---|---|---|---|---|---|---|"]
    def _due(f):
        d = f.deadline or {}
        return "" if not d else (f"**OVERDUE** {d['due'][:10]}" if d["overdue"] else d["due"][:10])
    md += [f"| {f.severity} | {f.agent} | {f.kind} | `{f.entity}` | {inr(f.amount)} | {'✓' if f.counted else '–'} | {_due(f)} | "
           f"{f.action or ''}{' 🔒' if f.needs_approval else ''} | {f.summary} |" for f in fs]
    if F["results"]:
        md += ["\n## Approved actions\n"] + [f"- {r['id']}: **{r['status']}** {json.dumps({k: v for k, v in r.items() if k not in ('id', 'status')}, default=str)[:220]}" for r in F["results"]]
    open(os.path.join(d, "REPORT.md"), "w").write("\n".join(md) + "\n")
    print("\nScore vs ground truth:")
    for r in headline(S):
        print(f"  {r[0]:9s} {r[1]:30s} found {r[2]:7s} fp {str(r[3]):3s} P {str(r[4]):5s} R {r[5]}")
    for r in F["results"]:
        print(f"  action {r['id']}: {r['status']}")
    print(f"\nsaved {d}  ({elapsed:.0f}s)")


if __name__ == "__main__":
    main()
