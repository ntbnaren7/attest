"""LangGraph orchestrator.

    load ─┬─ recon ────┐
          ├─ leakage ──┤
          ├─ recovery ─┼─ triage ─ explain ─ approve (interrupt: human decides) ─ execute ─ report
          └─ payouts ──┘

Agents are deterministic tools; the graph adds fan-out, de-duplication across agents, ranking, an LLM-free
default explanation (Sarvam can be plugged in), and a hard human-approval gate before any money movement.
"""
import operator
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, Dict, List, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import actions
from ..sources import cashfree as cfio, ledger
from ..agents import AGENTS
from .explain import narrate
from datetime import datetime

from ..lib.dates import IST
from ..lib.deadlines import deadline
from ..lib.findings import Finding, exposure, mark_counted, ranked


class State(TypedDict, total=False):
    run: str
    agents: List[str]
    execute: bool
    findings: Annotated[List[dict], operator.add]
    stats: Annotated[Dict[str, Any], lambda a, b: {**(a or {}), **(b or {})}]
    brief: str
    brief_source: str
    approvals: Dict[str, bool]
    results: List[dict]


_LEDGERS = {}
LLM = None  # set graph.LLM = callable(prompt)->str to let Sarvam narrate


def _ledger(run):
    if run not in _LEDGERS:
        _LEDGERS[run] = ledger.load(run)
    return _LEDGERS[run]


def load(state: State):
    L = _ledger(state["run"])
    with ThreadPoolExecutor(8) as ex:  # warm the Cashfree cache in parallel (~40 orders x 3-4 calls)
        list(ex.map(cfio.order_snapshot, [o["order_id"] for o in L["orders"]]))
        list(ex.map(cfio.transfer, [p["transfer_id"] for p in L["payouts"]]))
    return {"stats": {"orders": len(L["orders"]), "payouts": len(L["payouts"]), "requests": len(L["payout_requests"])}}


def _agent_node(name):
    def node(state: State):
        if name not in state.get("agents", list(AGENTS)):
            return {"findings": []}
        ctx = {"ledger": _ledger(state["run"])}
        fs = AGENTS[name](ctx)
        return {"findings": [f.to_dict() for f in fs], "stats": {k: v for k, v in ctx.items() if k.endswith("_stats") or k == "tieout"}}
    node.__name__ = name
    return node


def triage(state: State):
    """Cross-agent guard rails:
    * link findings about the same entity (e.g. recon FULFILLED_BUT_UNPAID + recovery RECOVER),
    * attach a deadline (with its legal/policy basis) to every actionable finding,
    * count each rupee once: findings with the same money_key are the same money; only one counts toward ₹ at stake."""
    by_entity = {}
    for f in state["findings"]:
        by_entity.setdefault(f["entity"], []).append(f)
    for ent, fs in by_entity.items():
        if len(fs) > 1:
            for f in fs:
                f["evidence"]["related"] = [f"{g['agent']}:{g['kind']}" for g in fs if g is not f]
    now = state.get("now") or datetime.now(IST)
    objs = [Finding(**f) for f in state["findings"]]
    mark_counted(objs)
    for f, o in zip(state["findings"], objs):
        f["counted"] = o.counted
        f["deadline"] = deadline(f["kind"], f["evidence"], now)
    total, groups = exposure(objs)
    return {"stats": {"exposure": dict(at_stake=total, distinct_items=len(groups),
                                       same_money_merged=sum(len(g) - 1 for g in groups.values()))}}


def explain(state: State):
    fs = [Finding(**f) for f in state["findings"]]
    text, src = narrate(fs, llm=LLM, tieout=(state.get("stats") or {}).get("tieout"))
    return {"brief": text, "brief_source": src}


def approve(state: State):
    L = _ledger(state["run"])
    pending = [dict(id=f"{f['agent']}:{f['entity']}", action=f["action"], amount=f["amount"], summary=f["summary"],
                    plan=actions.plan(f, L)) for f in state["findings"] if f.get("needs_approval")]
    decision = interrupt({"pending_approvals": pending})  # graph pauses here until a human answers
    if decision == "all":
        decision = {p["id"]: True for p in pending}
    elif decision in (None, "none"):
        decision = {}
    return {"approvals": decision}


def execute(state: State):
    L = _ledger(state["run"]); res = []
    for f in state["findings"]:
        key = f"{f['agent']}:{f['entity']}"
        if f.get("needs_approval") and state.get("approvals", {}).get(key):
            res.append(dict(id=key, **actions.execute(f, L, execute=state.get("execute", False))))
    return {"results": res}


def build():
    g = StateGraph(State)
    g.add_node("load", load)
    for name in AGENTS:
        g.add_node(name, _agent_node(name))
        g.add_edge("load", name)
        g.add_edge(name, "triage")
    g.add_node("triage", triage); g.add_node("explain", explain); g.add_node("approve", approve); g.add_node("execute", execute)
    g.add_edge(START, "load"); g.add_edge("triage", "explain"); g.add_edge("explain", "approve")
    g.add_edge("approve", "execute"); g.add_edge("execute", END)
    return g.compile(checkpointer=MemorySaver())
