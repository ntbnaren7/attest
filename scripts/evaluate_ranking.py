"""Compare our contact order with the file's original order once true outcomes are known.

  .venv/bin/python scripts/evaluate_ranking.py fixtures/borrower_batch_B_fresh.csv --outcome-col paid_within_7d --k 3

The outcome column is held out by the importer (never stored or scored). This script imports the
file into a throwaway database, ranks it, then reads the outcomes from the original file.
Reports: payers reached in the first k calls (ours vs file order), and accounts we held or sent to
review that the file order would have called.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RECEIVABLES_NO_AUTOAPP"] = "1"

from app.config import Settings  # noqa: E402
from app.context import Ctx  # noqa: E402
from app.importer import ALIASES, _norm_header, import_batch  # noqa: E402
from app.main import seed  # noqa: E402
from app.queue import build_queue  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--outcome-col", default="paid_within_7d")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--merchant", default="lender_demo")
    a = ap.parse_args()
    raw = Path(a.file).read_bytes()
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    id_col = next(h for h in rows[0] if _norm_header(h) in ALIASES["account_ref"])
    outcome = {r[id_col]: str(r.get(a.outcome_col, "")).strip() in ("1", "true", "yes", "paid") for r in rows}
    s = Settings()
    s.db_path = tempfile.mktemp(suffix=".db")
    ctx = Ctx(s)
    seed(ctx)
    rep = import_batch(ctx, a.merchant, Path(a.file).name, raw, "evaluator")
    q = build_queue(ctx, a.merchant, rep["batch_id"], record_changes=False)
    ours = [r["external_ref"] for r in q["rows"] if r["decision"] == "CONTACT"]
    file_order = [r[id_col] for r in rows if r[id_col] in {x["external_ref"] for x in q["rows"]}]
    k = min(a.k, len(file_order))
    hit = lambda order: sum(outcome.get(x, False) for x in order[:k])
    print(f"accounts={len(file_order)} payers={sum(outcome.get(x, False) for x in file_order)} k={k}")
    print(f"payers reached in first {k} calls: ours={hit(ours)}  file order={hit(file_order)}")
    held = [r for r in q["rows"] if r["decision"] != "CONTACT"]
    print("held / review (not called) with reasons:")
    for r in held:
        print(f"  {r['external_ref']}: {r['decision']} {r['code']} (true outcome paid={outcome.get(r['external_ref'])})")


if __name__ == "__main__":
    main()
