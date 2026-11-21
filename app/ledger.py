"""Scan a photographed ledger / collection-diary page.

Sarvam Document AI (ocr/) reads the page; each line is matched to an account in this merchant's book by its id
(L-1004, B-503) or by a name that is unique in the book, then goes through the same coordinator turn as a typed
note: claims, promises, hardship and do-not-call are recorded as claims (never as facts) and the queue re-forms.
"""
from __future__ import annotations

import re

from . import coordinator, customers, inbox
from .context import Ctx

REF = re.compile(r"\b([A-Z]{1,4})[\s\-/]?(\d{2,6})\b", re.IGNORECASE)
MAX_LINES = 25


def _norm(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


def match_line(rows: list[dict], line: str) -> dict | None:
    for m in REF.finditer(line):
        hit = [r for r in rows if _norm(r["external_ref"]) == _norm(m.group(1) + m.group(2))]
        if len(hit) == 1:
            return hit[0]
    low = line.lower()
    full = [r for r in rows if r["display_name"] and r["display_name"].lower() in low]
    if len(full) == 1:
        return full[0]
    first = [r for r in rows if r["display_name"]
             and re.search(rf"\b{re.escape(r['display_name'].split()[0].lower())}\b", low)]
    return first[0] if len(first) == 1 else None


def lines_of(text: str) -> list[str]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip(" \t-•*|#>")
        if len(line) >= 6 and re.search(r"[^\W\d_]", line):
            out.append(line)
    return out[:MAX_LINES]


def scan(ctx: Ctx, op, image: bytes, filename: str, language: str = "en-IN") -> dict:
    ocr = inbox.read_image(ctx, image, filename)
    text = (ocr.get("extracted_text") or "").strip()
    rows = ctx.db.all("SELECT id, external_ref, display_name FROM accounts WHERE merchant_id=?", (op.merchant_id,))
    results = []
    for line in lines_of(text):
        acct = match_line(rows, line)
        if not acct:
            results.append({"line": line, "account": None, "proposed": customers.propose(line)})
            continue
        turn = coordinator.process(ctx, op, f"{acct['external_ref']}: {line}", via="ledger_photo",
                                   account_hint=acct["id"], speak=False, reply_language=language)
        d = turn.get("decision") or {}
        results.append({"line": line, "account": {"id": acct["id"], "external_ref": acct["external_ref"],
                                                  "display_name": acct["display_name"]},
                        "intent": (turn.get("extraction") or {}).get("intent"), "reply": turn.get("reply"),
                        "decision": d.get("decision"), "code": d.get("code"),
                        "recorded": [h["summary"] for h in turn["handoffs"] if h["action"] == "record_claim"]})
    matched = sum(1 for r in results if r["account"])
    ctx.db.trace(op.merchant_id, "ledger_scan", f"operator:{op.username}", target="ocr",
                 result={"lines": len(results), "matched": matched, "chars": len(text)})
    return {"ocr_text": text, "review_required": bool(ocr.get("review_required")), "lines": results, "matched": matched}
