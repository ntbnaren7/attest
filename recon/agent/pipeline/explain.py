"""Explanation layer. The LLM (Sarvam, via teammates) only *narrates* findings; it never computes or decides.

    narrate(findings, llm=None, language="en")
      llm: optional callable(prompt:str) -> str  (plug Sarvam here). Without it, a template brief is returned.

Guardrail: every ₹ amount in the LLM's text must exist in the findings' evidence, otherwise we fall back to the
template (an LLM must never invent or round money figures).
"""
import json
import re

from ..lib.findings import exposure, ranked
from ..lib.money import inr, paise

AMT = re.compile(r"₹\s?([\d,]+(?:\.\d{1,2})?)")


def _allowed_amounts(findings):
    vals = set()
    def walk(x):
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            vals.add(paise(x))
        elif isinstance(x, dict):
            for v in x.values(): walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x: walk(v)
    for f in findings:
        walk(f.amount); walk(f.evidence); walk(f.priority)
        for m in AMT.finditer(f.summary):
            vals.add(paise(m.group(1).replace(",", "")))
    return vals


def numbers_grounded(text, findings):
    allowed = _allowed_amounts(findings)
    bad = [m.group(0) for m in AMT.finditer(text) if paise(m.group(1).replace(",", "")) not in allowed]
    return not bad, bad


def template_brief(findings, top=12, tieout=None):
    fs = ranked(findings)
    at_stake, groups = exposure(fs)  # each rupee counted once (same definition as the dashboard KPI)
    lines = [f"{len(fs)} findings; {inr(at_stake)} at stake across {len(groups)} distinct items (each rupee counted once)."]
    if tieout is not None:
        lines.append("Tie-out: every rupee between books, Cashfree and bank is explained by the findings below."
                     if tieout.get("ok") else "Tie-out BROKEN: some money is unexplained. Do not sign off the books.")
    lines.append("")
    for f in fs[:top]:
        dl = f.deadline or {}
        due = (f" [OVERDUE since {dl['due'][:10]}]" if dl.get("overdue") else f" [due {dl['due'][:10]}]") if dl else ""
        lines.append(f"- [{f.severity.upper()}] ({f.agent}) {f.summary} → {f.action or 'review'}{due}"
                     + (" (needs your approval)" if f.needs_approval else ""))
    return "\n".join(lines)


def narrate(findings, llm=None, language="en", top=12, tieout=None):
    base = template_brief(findings, top, tieout)
    if not llm:
        return base, "template"
    facts = [dict(agent=f.agent, kind=f.kind, amount=f.amount, severity=f.severity, summary=f.summary, action=f.action)
             for f in ranked(findings)[:top]]
    prompt = (f"You are a finance operations assistant for an Indian merchant. Write a short brief in language '{language}' "
              f"for the business owner. Use ONLY these facts; copy every rupee amount exactly as written; do not add numbers.\n"
              + json.dumps(facts, ensure_ascii=False))
    try:
        text = llm(prompt)
    except Exception as e:  # never let the explainer break the pipeline
        return base + f"\n(LLM unavailable: {e})", "template"
    ok, bad = numbers_grounded(text, findings)
    if not ok:
        return base + f"\n(LLM text discarded: ungrounded amounts {bad})", "template"
    return text, "llm"
