"""Adding customers by hand or from a scanned ledger page.

New customers go through the normal importer as a one-off list, so they get the same validation, fairness
filtering (no protected columns), note-reading and queue update as any uploaded file.
"""
from __future__ import annotations

import csv
import io
import re

from . import importer
from .context import Ctx
from .money import AmountError, parse_rupees_to_paise

NAME = re.compile(r"^\s*([^\W\d_][^\W\d_.'\s]*(?:[ .'][^\W\d_][^\W\d_.'\s]*){0,3})", re.UNICODE)
AMOUNT = re.compile(r"(?:₹|\brs\.?|\binr)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)|\b([0-9]{1,3}(?:,[0-9]{2,3})+|[0-9]{3,7})(?:\.[0-9]{1,2})?\b(?!\s*(?:th|st|nd|rd|/|-\d))",
                    re.IGNORECASE)
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")
SKIP = re.compile(r"diary|ledger|date|page|total|collection|summary|^\d", re.IGNORECASE)


def propose(line: str) -> dict | None:
    """Guess a new customer (name, amount, phone) from a ledger line that matched no account."""
    m = NAME.match(line)
    if not m or SKIP.search(line.split("-")[0]):
        return None
    name = m.group(1).strip()
    if len(name) < 3:
        return None
    phone = PHONE.search(line)
    rest = PHONE.sub(" ", line[m.end():])
    amt = None
    for a in AMOUNT.finditer(rest):
        try:
            amt = parse_rupees_to_paise(a.group(1) or a.group(2))
            break
        except AmountError:
            continue
    return {"name": name, "amount_paise": amt, "phone": phone.group(1) if phone else "", "note": line.strip()}


def _next_ref(ctx: Ctx, merchant_id: str) -> tuple[str, int]:
    mode = ctx.db.one("SELECT mode FROM merchants WHERE id=?", (merchant_id,))["mode"]
    prefix = "B-" if mode == "merchant" else "L-"
    nums = [int(re.sub(r"\D", "", r["external_ref"]) or 0) for r in
            ctx.db.all("SELECT external_ref FROM accounts WHERE merchant_id=? AND external_ref LIKE ?", (merchant_id, prefix + "%"))]
    return prefix, max(nums + [2000]) + 1


def add(ctx: Ctx, merchant_id: str, items: list[dict], actor: str, source: str = "added_by_hand") -> dict:
    prefix, n = _next_ref(ctx, merchant_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["account_id", "name", "amount", "phone", "language", "due_date", "notes"])
    refs = []
    for i, it in enumerate(items[:50]):
        ref = f"{prefix}{n + i}"
        refs.append(ref)
        w.writerow([ref, str(it.get("name", "")).strip()[:80], str(it.get("amount", "")).strip(),
                    re.sub(r"\D", "", str(it.get("phone", "")))[-10:], str(it.get("language", "")).strip(),
                    str(it.get("due_date", "")).strip(), str(it.get("note", "")).strip()[:300]])
    rep = importer.import_batch(ctx, merchant_id, f"{source}.csv", buf.getvalue().encode(), actor)
    rep["refs"] = refs
    return rep
