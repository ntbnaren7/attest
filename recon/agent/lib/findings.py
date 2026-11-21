"""One record type shared by every agent, the orchestrator, the explainer and the evaluator."""
from dataclasses import asdict, dataclass, field
from typing import Optional

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
# findings that describe a decision or a state, not money lost or at risk
NOT_MONEY = {"PAYOUT_DECISION", "PAYOUT_IN_FLIGHT", "MATCHED_WITH_MANGLED_REF"}
AT_STAKE_SEVERITIES = ("critical", "high")


@dataclass
class Finding:
    agent: str                      # recon | leakage | recovery | payouts
    kind: str                       # e.g. CAPTURED_NOT_SETTLED, DUPLICATE_PAYOUT, RECOVER, PAYOUT_DECISION
    entity: str                     # order_id / transfer_id / request_id / settlement utr
    amount: float                   # ₹ at stake (0 if none)
    severity: str                   # critical | high | medium | low | info
    summary: str                    # one line, numbers must come from evidence
    evidence: dict = field(default_factory=dict)
    action: Optional[str] = None    # recommended action code
    needs_approval: bool = False    # money movement that a human must OK
    priority: float = 0.0           # higher = do first (₹ impact x likelihood)
    money_key: str = ""             # identifies the underlying rupees; findings sharing a key are the SAME money
    deadline: Optional[dict] = None  # {due, days_left, overdue, basis} (attached by the orchestrator)
    counted: bool = True            # False when another finding already counts the same money

    def to_dict(self):
        return asdict(self)


def _key(f):
    return f.money_key or f"{f.agent}:{f.kind}:{f.entity}"


def exposure(findings):
    """₹ at stake with every rupee counted once: group by money_key, take the largest amount in each group.
    Returns (total, {money_key: [findings]})."""
    groups = {}
    for f in findings:
        if f.severity in AT_STAKE_SEVERITIES and f.kind not in NOT_MONEY and f.amount > 0:
            groups.setdefault(_key(f), []).append(f)
    return round(sum(max(x.amount for x in g) for g in groups.values()), 2), groups


def mark_counted(findings):
    """Set .counted on each finding: exactly one finding per money group counts toward ₹ at stake."""
    _, groups = exposure(findings)
    primary = {}
    for k, g in groups.items():
        primary[k] = min(g, key=lambda x: (-x.amount, SEVERITY_RANK.get(x.severity, 9)))
    for f in findings:
        p = primary.get(_key(f))
        f.counted = p is f
        if p is not None and p is not f:
            f.evidence["counted_with"] = f"{p.agent}:{p.kind}:{p.entity}"
    return findings


def ranked(findings):
    return sorted(findings, key=lambda f: (SEVERITY_RANK.get(f.severity, 9), not (f.deadline or {}).get("overdue", False),
                                           -f.priority, -f.amount))
