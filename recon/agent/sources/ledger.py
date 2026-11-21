"""The merchant's own books: what the business *thinks* happened. Agents compare this to Cashfree.

Built from a seed run's truth.json with every label stripped, plus planted bookkeeping errors and a bank
statement. The plants are recorded in ledger_truth.json (for scoring only; agents never read it).

    python -m agent.sources.ledger build 20261120_094955
"""
import csv
import glob
import json
import os
import random
import re
import sys

from . import cashfree as cfio
from ..config import ROOT, SEEDS


def run_dir(run):
    return os.path.join(SEEDS, f"seed_{run}")


def merchant_dir(run):
    return os.path.join(run_dir(run), "merchant")


PAYEE_PREFIX = re.compile(r"^(Invoice settlement|Invoice|Bulk order|Weekly payout|INV-\d+)\s+")


def build(run, seed=7):
    rng = random.Random(seed)
    T = json.load(open(os.path.join(run_dir(run), "truth.json")))
    out = merchant_dir(run); os.makedirs(out, exist_ok=True)
    lt = {"ledger_status": [], "bank": []}

    # ── sales ledger: merchant marks an order PAID when its checkout callback said so ──
    paid_like = {"paid", "recovered_after_retry", "paid_but_unsettled"}
    orders = []
    for o in T["orders"]:
        status = "PAID" if o["scenario"] in paid_like else "UNPAID"
        orders.append(dict(order_id=o["order_id"], customer=o["customer"], amount=o["amount"], ledger_status=status,
                           fulfilled=status == "PAID"))
    # plant: 2 unpaid orders the merchant shipped as PAID (callback bug), 1 paid order still UNPAID in books
    unpaid = [x for x in orders if x["ledger_status"] == "UNPAID" and x["amount"] < 10_000]
    paid = [x for x in orders if x["ledger_status"] == "PAID"]
    for x in rng.sample(unpaid, 2):
        x.update(ledger_status="PAID", fulfilled=True)
        lt["ledger_status"].append(dict(order_id=x["order_id"], kind="FULFILLED_BUT_UNPAID", amount=x["amount"]))
    x = rng.choice([p for p in paid if p["order_id"] not in {u["order_id"] for u in T["orders"] if u["scenario"] == "paid_but_unsettled"}])
    x.update(ledger_status="UNPAID", fulfilled=False)
    lt["ledger_status"].append(dict(order_id=x["order_id"], kind="PAID_NOT_FULFILLED", amount=x["amount"]))

    refunds = [dict(order_id=r["order_id"], amount=r["refund_amount"], reason="Damaged item" if "duplicate" in r["label"] else "Customer request",
                    refund_id=r["refund_id"]) for r in T["refunds"] if r["api_accepted"]]

    # ── payout register: who the merchant paid, for which invoice, and who approved ──
    payouts = []
    for k, p in enumerate(T["payouts"]):
        if not p["transfer_id"]:
            continue
        inv = "INV-2231" if p["kind"].startswith("duplicate") else f"INV-{1100 + k}"
        payouts.append(dict(transfer_id=p["transfer_id"], beneficiary_id=p["beneficiary_id"], payee=PAYEE_PREFIX.sub("", p["label"]).strip(),
                            invoice=inv, amount=p["amount"], approved_by=None, created=T["run"]))

    # ── bank statement: Cashfree settlement credits mixed into ordinary business activity ──
    batches = cfio.settlement_batches(days=3)
    rows, ours = [], None
    for b in batches:
        utr = b.get("utr") or f"UTR{b['cf_settlement_id']}"
        amt = float(b["amount_settled"]); narr = f"NEFT Cr-{utr}-YESB0000001-CASHFREE PAYMENTS INDIA PVT LTD"
        date = (b.get("settlement_date") or "")[:10]
        rows.append(dict(date=date, narration=narr, credit=amt, utr=utr, cf_settlement_id=b["cf_settlement_id"]))
    run_ids = {o["order_id"] for o in T["orders"]}
    ours_id = next((str(x["settlement_details"]["cf_settlement_id"]) for x in cfio.recon_feed(3)
                    if (x.get("order_details") or {}).get("order_id") in run_ids and (x.get("settlement_details") or {}).get("cf_settlement_id")), None)
    for r in rows:
        if str(r["cf_settlement_id"]) == ours_id:
            r["credit"] = round(r["credit"] - 37.50, 2)
            lt["bank"].append(dict(utr=r["utr"], cf_settlement_id=r["cf_settlement_id"], kind="SHORT_CREDIT", delta=37.50))
        elif len(lt["bank"]) < 2 and float(r["credit"]) > 1000:
            r["narration"] = r["narration"].replace(r["utr"], r["utr"][:-3] + " " + r["utr"][-3:-1] + "X")
            lt["bank"].append(dict(utr=r["utr"], cf_settlement_id=r["cf_settlement_id"], kind="MANGLED_REF_SHOULD_MATCH", delta=0))
    # distractors: real-format credits from a public Indian bank statement (AgamiAI, Apache-2.0)
    agami = json.load(open(sorted(glob.glob(os.path.join(ROOT, "research", "real_data", "data", "agami", "00001.json")))[0]))
    for t in [t for t in agami["transactions"] if t.get("credit")][:25]:
        rows.append(dict(date=rows[0]["date"] if rows else "", narration=t["description"], credit=t["credit"], utr="", cf_settlement_id=""))
    rng.shuffle(rows)
    with open(os.path.join(out, "bank_statement.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "narration", "credit"]); w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in ("date", "narration", "credit")})

    import datetime as _dt
    as_of = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).isoformat(timespec="seconds")
    ledger = dict(run=run, merchant="Seed Merchant", bank_statement_as_of=as_of, orders=orders, refunds=refunds, payouts=payouts,
                  payout_requests=json.load(open(os.path.join(run_dir(run), "payout_requests.json"))))
    for q in ledger["payout_requests"]:  # requests must not carry the answer
        q.pop("expected", None); q.pop("why", None)
    json.dump(ledger, open(os.path.join(out, "ledger.json"), "w"), indent=1)
    json.dump(lt, open(os.path.join(out, "ledger_truth.json"), "w"), indent=1)
    print(f"ledger: {len(orders)} orders, {len(refunds)} refunds, {len(payouts)} payouts, "
          f"{len(ledger['payout_requests'])} requests; bank rows {len(rows)} ({len(batches)} Cashfree credits); plants {lt}")
    return out


def load(run):
    d = merchant_dir(run)
    L = json.load(open(os.path.join(d, "ledger.json")))
    path = os.path.join(d, "bank_statement.csv")
    from ..lib.money import num  # real exports: debit and credit columns, blank cells, 'NA', thousands separators
    L["bank_statement"] = [dict(r, credit=num(r.get("credit")), debit=num(r.get("debit"))) for r in csv.DictReader(open(path))]
    if not L.get("bank_statement_as_of"):  # older ledgers: the statement covers up to when it was exported
        import datetime as _dt
        L["bank_statement_as_of"] = _dt.datetime.fromtimestamp(os.path.getmtime(path), _dt.timezone.utc).isoformat(timespec="seconds")
    return L


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "build":
        build(sys.argv[2], seed=int(sys.argv[3]) if len(sys.argv) > 3 else 7)
    else:
        print(__doc__)
