"""Score agent findings against the seed ground truth (truth.json + merchant/ledger_truth.json).

    python -m agent.eval <findings.json> [run]
"""
import json
import os
import sys
from collections import defaultdict

from .sources.ledger import merchant_dir, run_dir

RECOVERY_OK = {  # scenario of a shipped-but-unpaid order -> acceptable collection actions (no blind retries)
    "failed_bank_offline": {"PAYMENT_LINK"}, "failed_insufficient_funds": {"PAYMENT_LINK"}, "failed_user_decline": {"PAYMENT_LINK"},
    "user_dropped": {"PAYMENT_LINK", "FREE_NUDGE_ONLY"}, "abandoned_no_attempt": {"PAYMENT_LINK", "FREE_NUDGE_ONLY"},
    "retries_exhausted": {"PAYMENT_LINK"}, "high_value_failed": {"PAYMENT_LINK"}, "collect_expired": {"PAYMENT_LINK"},
}


def prf(found, truth):
    found, truth = set(found), set(truth)
    tp, fp, fn = len(found & truth), len(found - truth), len(truth - found)
    return dict(tp=tp, fp=fp, fn=fn, precision=round(tp / (tp + fp), 3) if tp + fp else 1.0,
                recall=round(tp / (tp + fn), 3) if tp + fn else 1.0, missed=sorted(truth - found), extra=sorted(found - truth))


def score(findings, run, stats=None):
    T = json.load(open(os.path.join(run_dir(run), "truth.json")))
    LT = json.load(open(os.path.join(merchant_dir(run), "ledger_truth.json")))
    by = defaultdict(list)
    for f in findings:
        by[(f["agent"], f["kind"])].append(f)
    ents = lambda agent, *kinds: {f["entity"] for k in kinds for f in by[(agent, k)]}
    S = {}
    # ── recon ──
    exp_unsettled = {o["order_id"] for o in T["orders"] if o["scenario"] == "paid_but_unsettled"}
    S["recon"] = dict(
        fulfilled_but_unpaid=prf(ents("recon", "FULFILLED_BUT_UNPAID"), {x["order_id"] for x in LT["ledger_status"] if x["kind"] == "FULFILLED_BUT_UNPAID"}),
        paid_not_fulfilled=prf(ents("recon", "PAID_NOT_FULFILLED"), {x["order_id"] for x in LT["ledger_status"] if x["kind"] == "PAID_NOT_FULFILLED"}),
        captured_not_settled=prf(ents("recon", "CAPTURED_NOT_SETTLED"), exp_unsettled),
        short_credit=prf(ents("recon", "SHORT_CREDIT"), {x["utr"].upper() for x in LT["bank"] if x["kind"] == "SHORT_CREDIT"}),
        missing_credit_false_alarms=len(by[("recon", "MISSING_CREDIT")]),
        short_credit_delta_exact=all(abs(f["amount"] - 37.5) < 0.005 for f in by[("recon", "SHORT_CREDIT")]),
        false_alarms_new_checks=sum(len(by[("recon", k)]) for k in ("AMOUNT_MISMATCH", "EXCESS_CREDIT", "DUPLICATE_CREDIT",
                                                                    "UNEXPLAINED_CASHFREE_CREDIT", "BATCH_TOTAL_MISMATCH")),
        tieout_ok=((stats or {}).get("tieout") or {}).get("ok", "n/a"),
        deadlines_on_actionable=f"{sum(1 for f in findings if f['agent'] == 'recon' and f.get('action') and f.get('deadline'))}"
                                f"/{sum(1 for f in findings if f['agent'] == 'recon' and f.get('action'))}",
        shipped_unpaid_counted_once=all(sum(1 for f in findings if f.get("money_key") == f"order:{oid}:collect" and f.get("counted")) == 1
                                        for oid in {x["order_id"] for x in LT["ledger_status"] if x["kind"] == "FULFILLED_BUT_UNPAID"}))
    # ── leakage ──
    dup_refund_orders = {r["order_id"] for r in T["refunds"] if r["label"] == "duplicate_refund_2nd" and r["api_accepted"]}
    S["leakage"] = dict(
        duplicate_refund=prf(ents("leakage", "DUPLICATE_REFUND"), dup_refund_orders),
        duplicate_payout=prf(ents("leakage", "DUPLICATE_PAYOUT"), {p["transfer_id"] for p in T["payouts"] if p["kind"] == "duplicate_2nd_LEAK"}),
        over_limit_no_approval=prf(ents("leakage", "PAYOUT_OVER_LIMIT_NO_APPROVAL"), {p["transfer_id"] for p in T["payouts"] if p["kind"] == "over_limit_no_approval"}),
        fee_gst_net_false_alarms=sum(len(by[("leakage", k)]) for k in ("FEE_OVERCHARGE", "GST_MISMATCH", "NET_SETTLEMENT_SHORT")),
        over_refund_false_alarms=len(by[("leakage", "OVER_REFUND")]))
    # ── recovery: only orders the books shipped as PAID that Cashfree never captured ──
    rec = {f["entity"]: f for f in by[("recovery", "RECOVER")]}
    scen = {o["order_id"]: o["scenario"] for o in T["orders"]}
    should = {x["order_id"] for x in LT["ledger_status"] if x["kind"] == "FULFILLED_BUT_UNPAID"}
    right = [oid for oid in should if oid in rec and rec[oid]["action"] in RECOVERY_OK.get(scen.get(oid), set())]
    S["recovery"] = dict(
        detected=prf(set(rec), should), correct_action=f"{len(right)}/{len(should)}",
        wrong_actions=[(scen.get(oid), rec[oid]["action"]) for oid in should if oid in rec and oid not in right],
        chased_unshipped_orders=sorted(set(rec) - should))  # must be []: ordinary failed payments belong to Relay
    # ── payouts ──
    dec = {f["entity"]: f["action"] for f in by[("payouts", "PAYOUT_DECISION")]}
    exp = {q["request_id"]: q["expected"] for q in T["payout_requests"]}
    wrong_dec = {k: (exp[k], dec.get(k)) for k in exp if dec.get(k) != exp[k]}
    kinds = {p["transfer_id"]: p["kind"] for p in T["payouts"]}
    S["payouts"] = dict(
        request_decisions=f"{len(exp) - len(wrong_dec)}/{len(exp)}", wrong_decisions=wrong_dec,
        wrongly_approved=[k for k, (e, g) in wrong_dec.items() if g == "APPROVE"],
        stuck=prf(ents("payouts", "STUCK_PAYOUT"), {t for t, k in kinds.items() if k == "stuck_pending"}),
        reversed=prf(ents("payouts", "REVERSED_PAYOUT"), {t for t, k in kinds.items() if k == "success_then_reversed"}),
        failed=prf(ents("payouts", "FAILED_PAYOUT"), {t for t, k in kinds.items() if k in ("failed_upi", "failed_invalid_account")}))
    return S


def headline(S):
    rows = []
    def add(agent, check, r):
        if isinstance(r, dict) and "tp" in r:
            rows.append((agent, check, f"{r['tp']}/{r['tp'] + r['fn']}", r["fp"], r["precision"], r["recall"]))
        else:
            rows.append((agent, check, str(r), "", "", ""))
    for agent, checks in S.items():
        for check, r in checks.items():
            if check in ("wrong_actions", "wrong_decisions"):
                continue
            add(agent, check, r)
    return rows


if __name__ == "__main__":
    F = json.load(open(sys.argv[1]))
    run = sys.argv[2] if len(sys.argv) > 2 else F["run"]
    S = score(F["findings"], run)
    for r in headline(S):
        print(f"{r[0]:9s} {r[1]:30s} found {r[2]:7s} fp {str(r[3]):3s} P {str(r[4]):5s} R {r[5]}")
