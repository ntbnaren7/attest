"""Reconciliation: merchant books vs Cashfree (gateway, settlements, deductions) vs the bank statement.

Deterministic. OpenRecon does the first-pass settlement-to-bank matching. Our guard rejects amount+date-only pairs that
lack reference evidence, and reference recovery (exact / truncated / garbled) pairs what the bank mangled.

What it covers (each traced to a real case in research/case_replay):
  * books vs Cashfree per order: shipped-unpaid, paid-unfulfilled, amount differs, duplicate payments (outage retries)
  * Cashfree captured vs settled, with settlement timing (in transit vs skipped vs overdue)
  * deduction lines inside settlements (chargeback, dispute, refund, adjustment) vs the books
  * settlement vs bank credit: missing, short, excess, duplicate, unexplained Cashfree credits (UCO 2023)
  * bank reversals (debits that pull an earlier credit back)
  * sales the books record as paid directly (UPI to the shop, bank transfer) vs bank credits (fake-screenshot fraud)

Every run ends with a TIE-OUT in integer paise. Any non-zero residual means some money is not explained by an itemised
finding: that raises TIEOUT_BREAK and the books must not be signed off.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta

from rapidfuzz import fuzz

from ..config import (CASHFREE_PAYER_RX, DIRECT_MODES, DUPLICATE_PAYMENT_WINDOW_MIN, MATCH_MIN_AMOUNT_ONLY_PAISE,
                      MATCH_MIN_FRAGMENT, MATCH_POLICY, MATCH_WINDOW_DAYS, REVERSAL_RX, SETTLEMENT_SLA_HOURS)
from ..lib.dates import IST, dec2, parse_date, parse_ts
from ..lib.findings import Finding
from ..lib.matching import match as match_txns
from ..lib.money import cf_round, inr, num, paise
from ..sources import cashfree as cfio
from ..vendor.openrecon.models import Transaction

REF_RX = re.compile(r"(?:NEFT Cr-|RTGS Cr-|NEFT-|RTGS-|IMPS-|UPI/CR/)([A-Z]{0,6}\d{9,22})[-/]", re.I)  # both real statement layouts
PG_RX = re.compile(CASHFREE_PAYER_RX, re.I)
REV_RX = re.compile(REVERSAL_RX, re.I)
MIN_PARTIAL_REF = 10      # a truncated reference must keep at least this many characters
FUZZY_REF = 75            # RapidFuzz partial_ratio needed to accept a garbled reference (benchmarked)
DEBIT_TYPES = {"REFUND", "DISPUTE", "CHARGEBACK", "OTHER_ADJUSTMENT"}
REVERSAL_OF = {"REFUND_REVERSAL": "REFUND", "DISPUTE_REVERSAL": "DISPUTE", "CHARGEBACK_REVERSAL": "CHARGEBACK"}


def _rs(p):
    return round(p / 100, 2)


def _strip(ref):
    ref = (ref or "").upper()
    return ref[3:] if ref.startswith("UTR") else ref


def ref_evidence(key, narration):
    """How strongly a bank narration refers to settlement `key`: (method, score) or (None, score)."""
    k, n = key.upper(), narration.upper()
    m = REF_RX.search(n)
    if m:
        b, kd = _strip(m.group(1)), _strip(k)
        if b == kd:
            return "exact_ref", 100
        if len(b) >= MIN_PARTIAL_REF and (kd.endswith(b) or kd.startswith(b)):
            return "truncated_ref", 95
    s = fuzz.partial_ratio(k, n)
    return (f"fuzzy_ref:{s:.0f}", s) if s >= FUZZY_REF else (None, s)


def _words(narration):
    """'UPI/CR/7751/AMIT SAHA/SENT-TRANSFER' -> 'UPI CR 7751 AMIT SAHA SENT TRANSFER' (real narrations glue words with / and -)."""
    return re.sub(r"[^A-Z0-9]+", " ", (narration or "").upper()).strip()


def _ref(narration):
    m = REF_RX.search(narration or "")
    return m.group(1).upper() if m else None


def _success(snap):
    return [p for p in snap["payments"] if p.get("payment_status") == "SUCCESS"]


def _captured(snap):
    pays = _success(snap)
    if not pays:
        return None, None
    return cf_round(sum(num(p.get("payment_amount") or p.get("order_amount")) for p in pays)), pays[-1].get("payment_time")


def _last_attempt_time(snap):
    pays = snap["payments"]
    return (pays[-1].get("payment_time") if pays else None) or (snap.get("order") or {}).get("created_at")


def _event_net(e):
    """Signed effect of one recon-feed line on the settlement (CREDIT +, DEBIT -). Tolerates 'NA' and blanks."""
    d = e.get("event_details") or {}
    v = abs(num(d.get("event_settlement_amount")))
    st = (d.get("sale_type") or "").upper()
    if st in ("DEBIT", "CREDIT"):  # sale_type decides: OTHER_ADJUSTMENT can be either (Cashfree recon docs)
        return -v if st == "DEBIT" else v
    return -v if d.get("event_type") in DEBIT_TYPES else v


def _composition(batch, feed_by_id, ledger_ids):
    """What Cashfree says is inside a settlement batch (from the /pg/settlement/recon feed)."""
    evs = feed_by_id.get(str(batch["cf_settlement_id"]), [])
    if not evs:
        return dict(available=False, note="no per-order rows in Cashfree's recon feed for this settlement")
    lines = []
    for e in evs:
        d = e.get("event_details") or {}
        oid = (e.get("order_details") or {}).get("order_id")
        lines.append(dict(order_id=oid, type=d.get("event_type"), amount=num(d.get("event_amount")), fee=num(d.get("event_service_charge")),
                          gst=num(d.get("event_service_tax")), net=round(_event_net(e), 2), in_merchant_books=oid in ledger_ids))
    pay = [x for x in lines if x["type"] == "PAYMENT"]
    return dict(available=True, events=len(lines), gross=_rs(sum(paise(x["amount"]) for x in pay)),
                fees=_rs(sum(paise(x["fee"]) for x in lines)), gst=_rs(sum(paise(x["gst"]) for x in lines)),
                net=_rs(sum(paise(x["net"]) for x in lines)), deductions=_rs(-sum(paise(x["net"]) for x in lines if x["net"] < 0)),
                deduction_lines=[x for x in lines if x["net"] < 0][:10],
                orders_in_books=sum(x["in_merchant_books"] for x in pay), orders_other_channels=sum(not x["in_merchant_books"] for x in pay),
                lines=lines[:25], lines_truncated=len(lines) > 25)


def _section(title, lines, residual_p, note=""):
    return dict(title=title, lines=[dict(label=l, amount=_rs(p)) for l, p in lines], residual=_rs(residual_p),
                ok=residual_p == 0, note=note)


def run_recon(ctx):
    L, out = ctx["ledger"], []
    now = ctx.get("now") or datetime.now(IST)
    cf_orders = [o for o in L["orders"] if o.get("mode") not in DIRECT_MODES]
    direct = [o for o in L["orders"] if o.get("mode") in DIRECT_MODES]
    ledger_ids = {o["order_id"] for o in cf_orders}
    bank = L["bank_statement"]
    for r in bank:
        r["credit"], r["debit"] = num(r.get("credit")), num(r.get("debit"))

    # statement period decides how far back settlements must be fetched (else old credits look unexplained)
    bank_dates = [d for d in (parse_date(r["date"]) for r in bank) if d]
    span = (max(bank_dates) - min(bank_dates)).days if bank_dates else 0
    all_batches = cfio.settlement_batches(days=max(3, span + 2))
    tills = [t for t in (parse_ts(b.get("payment_till")) for b in all_batches) if t]
    latest_till = max(tills) if tills else None

    # ═════ A + B: books vs Cashfree per order, captured vs settled ═════
    A = defaultdict(int); B = defaultdict(int); in_transit = []
    snaps = {o["order_id"]: cfio.order_snapshot(o["order_id"]) for o in cf_orders}
    gws = {oid: cfio.gateway_status(s) for oid, s in snaps.items()}
    window = timedelta(minutes=DUPLICATE_PAYMENT_WINDOW_MIN)

    def twin_of(o, cap, t):
        """Another paid order: same customer, same amount, paid within the window (an outage retry, not a new sale)."""
        for y in cf_orders:
            if y is o or gws[y["order_id"]] != "SUCCESS" or (y["customer"] or "").casefold() != (o["customer"] or "").casefold():
                continue
            ycap, yt = _captured(snaps[y["order_id"]])
            if paise(ycap) == paise(cap) and t and parse_ts(yt) and abs(parse_ts(yt) - t) <= window and y["ledger_status"] == "PAID":
                return y
        return None

    for o in cf_orders:
        oid = o["order_id"]; snap = snaps[oid]; gw = gws[oid]
        cap, cap_time = _captured(snap) if gw == "SUCCESS" else (None, None)
        books_paid = o["ledger_status"] == "PAID"
        if books_paid:
            A["books_paid"] += paise(o["amount"])
        if gw == "SUCCESS":
            A["captured"] += paise(cap)
        if books_paid and gw != "SUCCESS":
            A["fbu"] += paise(o["amount"])
            out.append(Finding("recon", "FULFILLED_BUT_UNPAID", oid, o["amount"], "critical",
                               f"Books say PAID and goods shipped, but Cashfree shows {gw}: {inr(o['amount'])} never collected.",
                               dict(ledger_status=o["ledger_status"], gateway_status=gw, customer=o["customer"],
                                    anchor_time=_last_attempt_time(snap),
                                    error=(snap["payments"][-1].get("error_details") or {}) if snap["payments"] else {}),
                               action="CHASE_OR_HOLD_DELIVERY", priority=o["amount"], money_key=f"order:{oid}:collect"))
        elif not books_paid and gw == "SUCCESS":
            A["pnf"] += paise(cap)
            twin = twin_of(o, cap, parse_ts(cap_time))
            if twin:
                out.append(Finding("recon", "DUPLICATE_PAYMENT", oid, cap, "high",
                                   f"{o['customer']} paid {inr(cap)} on this order and on {twin['order_id']} within {DUPLICATE_PAYMENT_WINDOW_MIN} min: "
                                   "a retry, not a new sale. Confirm with the customer and refund one; do NOT ship twice.",
                                   dict(first_order=twin["order_id"], customer=o["customer"], anchor_time=cap_time, ledger_status=o["ledger_status"]),
                                   action="CONFIRM_THEN_REFUND", priority=cap, money_key=f"order:{oid}:fulfil"))
            else:
                out.append(Finding("recon", "PAID_NOT_FULFILLED", oid, cap, "high",
                                   f"Customer paid {inr(cap)} (Cashfree SUCCESS) but books still show {o['ledger_status']}: order not fulfilled.",
                                   dict(ledger_status=o["ledger_status"], gateway_status=gw, customer=o["customer"], anchor_time=cap_time),
                                   action="FULFIL_ORDER", priority=cap * 0.8, money_key=f"order:{oid}:fulfil"))
        elif books_paid and gw == "SUCCESS" and paise(cap) != paise(o["amount"]):
            d = paise(cap) - paise(o["amount"]); A["mismatch"] += d
            pays = _success(snap)
            if len(pays) > 1 and d > 0:
                out.append(Finding("recon", "DUPLICATE_PAYMENT", oid, _rs(d), "high",
                                   f"{o['customer']} was charged {len(pays)} times on one order ({inr(cap)} captured vs {inr(o['amount'])} sale): "
                                   f"refund the extra {inr(_rs(d))}.",
                                   dict(payments=[dict(cf_payment_id=p.get("cf_payment_id"), amount=p.get("payment_amount"), time=p.get("payment_time")) for p in pays],
                                        customer=o["customer"], anchor_time=cap_time),
                                   action="CONFIRM_THEN_REFUND", priority=_rs(d), money_key=f"order:{oid}:dup"))
            else:
                under = d < 0
                out.append(Finding("recon", "AMOUNT_MISMATCH", oid, _rs(abs(d)), "high" if under else "medium",
                                   f"Books record {inr(o['amount'])} but Cashfree captured {inr(cap)}: "
                                   + (f"{inr(_rs(-d))} was never collected." if under else f"customer paid {inr(_rs(d))} more than the books show."),
                                   dict(books_amount=o["amount"], captured=cap, customer=o["customer"], anchor_time=cap_time),
                                   action="COLLECT_DIFFERENCE" if under else "CORRECT_BOOKS_OR_REFUND", priority=_rs(abs(d)),
                                   money_key=f"order:{oid}:collect"))
        if gw != "SUCCESS":
            continue
        B["captured"] += paise(cap)
        s = snap["settlement"] or {}
        if s.get("cf_settlement_id"):
            B["settled_gross"] += paise(cap); B["fees"] += paise(num(s.get("service_charge")))
            B["gst"] += paise(num(s.get("service_tax"))); B["net"] += paise(num(s.get("settlement_amount")))
            continue
        pt = parse_ts(s.get("payment_time") or cap_time)
        skipped = bool(latest_till and pt and latest_till >= pt)
        age_h = (now - pt).total_seconds() / 3600 if pt else None
        if skipped or age_h is None or age_h > SETTLEMENT_SLA_HOURS:
            B["overdue"] += paise(cap)
            why = (f"a later Cashfree settlement already covered payments captured up to {latest_till.isoformat(timespec='minutes')}, so this one was skipped"
                   if skipped else ("capture time unknown" if age_h is None else f"{age_h:.0f}h since capture, beyond the {SETTLEMENT_SLA_HOURS}h settlement cycle"))
            out.append(Finding("recon", "CAPTURED_NOT_SETTLED", oid, cap, "high",
                               f"Payment of {inr(cap)} captured but not in any Cashfree settlement: {why}.",
                               dict(payment_time=s.get("payment_time") or cap_time, settlement_amount_due=s.get("settlement_amount"),
                                    reason="skipped_by_later_settlement" if skipped else "past_settlement_cycle", anchor_time=s.get("payment_time") or cap_time),
                               action="RAISE_WITH_CASHFREE", priority=cap, money_key=f"order:{oid}:settle"))
        else:
            B["in_transit"] += paise(cap); in_transit.append(oid)

    # ═════ C0: deduction lines inside settlements vs the books ═════
    feed = cfio.recon_feed(max(3, span + 2))
    feed_by_id = defaultdict(list)
    for e in feed:
        sid = (e.get("settlement_details") or {}).get("cf_settlement_id")
        if sid:
            feed_by_id[str(sid)].append(e)
    K = defaultdict(int)
    by_order = defaultdict(lambda: defaultdict(int))  # order -> type -> net paise deducted (reversals netted)
    when = {}
    for e in feed:
        d = e.get("event_details") or {}; et = d.get("event_type") or ""
        oid = (e.get("order_details") or {}).get("order_id")
        base = REVERSAL_OF.get(et, et)
        if base not in DEBIT_TYPES:
            continue
        v = -paise(_event_net(e))  # deduction as a positive number; reversals come back negative
        if oid not in ledger_ids:
            K["other_channels"] += v; continue
        by_order[oid][base] += v; when[oid] = (e.get("settlement_details") or {}).get("settlement_date") or d.get("event_time")
    books_refunds = defaultdict(list)
    for r in L.get("refunds", []):
        books_refunds[r["order_id"]].append(paise(r["amount"]))
    for oid, types in by_order.items():
        o = next(x for x in cf_orders if x["order_id"] == oid)
        cb = types.get("CHARGEBACK", 0) + types.get("DISPUTE", 0)
        K["chargebacks"] += cb
        if cb > 0:
            out.append(Finding("recon", "CHARGEBACK_DEDUCTED", oid, _rs(cb), "critical",
                               f"Cashfree deducted {inr(_rs(cb))} from a settlement for a chargeback/dispute on {oid} ({o['customer']}), "
                               "but the books still count the sale as paid. The bank credit matches the smaller settlement, so nothing looks short.",
                               dict(customer=o["customer"], deducted=types, settlement_date=when.get(oid), anchor_time=when.get(oid)),
                               action="CONTEST_OR_ACCEPT_DISPUTE", priority=_rs(cb), money_key=f"order:{oid}:chargeback"))
        rf = types.get("REFUND", 0)
        if rf:
            left = list(books_refunds.get(oid, []))
            # refunds the books already know about explain the deduction; the rest is unrecorded
            for amt in sorted(left, reverse=True):
                if amt <= rf:
                    rf -= amt; K["refunds_in_books"] += amt
            if rf > 0:
                K["refunds_not_in_books"] += rf
                out.append(Finding("recon", "REFUND_NOT_IN_BOOKS", oid, _rs(rf), "high",
                                   f"Cashfree deducted a {inr(_rs(rf))} refund on {oid} ({o['customer']}) from a settlement, but the books have no such refund.",
                                   dict(customer=o["customer"], books_refunds=[_rs(x) for x in books_refunds.get(oid, [])], settlement_date=when.get(oid),
                                        anchor_time=when.get(oid)),
                                   action="RECORD_REFUND_OR_INVESTIGATE", priority=_rs(rf), money_key=f"order:{oid}:refund"))
        adj = types.get("OTHER_ADJUSTMENT", 0)
        if adj > 0:
            K["adjustments"] += adj
            out.append(Finding("recon", "ADJUSTMENT_DEDUCTED", oid, _rs(adj), "medium",
                               f"Cashfree deducted an adjustment of {inr(_rs(adj))} on {oid} from a settlement. Ask Cashfree what it is for.",
                               dict(settlement_date=when.get(oid), anchor_time=when.get(oid)), action="RAISE_WITH_CASHFREE", priority=_rs(adj),
                               money_key=f"order:{oid}:adjustment"))
        elif adj < 0:
            K["adjustments"] += adj
    K["total"] = sum(v for t in by_order.values() for v in t.values())

    # ═════ D: sales the books record as paid directly (not via Cashfree) vs bank credits ═════
    credits = [(i, r) for i, r in enumerate(bank) if r["credit"] > 0]
    used = set()
    Dd = defaultdict(int); direct_hits = {}
    for o in direct:
        oid = o["order_id"]
        if o.get("mode") == "CASH" or o["ledger_status"] != "PAID":
            Dd["cash"] += paise(o["amount"]) if o.get("mode") == "CASH" and o["ledger_status"] == "PAID" else 0
            continue
        Dd["books"] += paise(o["amount"])
        ref = (o.get("ref") or "").upper()
        hit = next(((i, r) for i, r in credits if i not in used and ref and len(ref) >= 9 and _ref(r["narration"]) and
                    (_strip(_ref(r["narration"])) == _strip(ref))), None)
        how = "utr"
        if not hit:  # no usable reference: same amount + payer name in narration, guarded
            how = "amount+name"
            name = (o.get("customer") or "").upper()
            hit = next(((i, r) for i, r in credits if i not in used and paise(r["credit"]) == paise(o["amount"]) and name
                        and len(name) > 3 and fuzz.token_set_ratio(name, _words(r["narration"])) >= 90), None)
        anchor = o.get("date") or L.get("bank_statement_as_of")
        if hit:
            i, r = hit; used.add(i); direct_hits[i] = oid
            got = paise(r["credit"]); short = paise(o["amount"]) - got
            Dd["verified"] += min(got, paise(o["amount"]))
            if short > 0:
                Dd["short"] += short
                out.append(Finding("recon", "DIRECT_PAYMENT_SHORT", oid, _rs(short), "critical",
                                   f"Books record {inr(o['amount'])} received from {o.get('customer')} by {o.get('mode')}, but the bank credit with that "
                                   f"reference is only {inr(r['credit'])}: {inr(_rs(short))} missing. An edited payment screenshot looks exactly like this.",
                                   dict(ref=ref, narration=r["narration"], bank_credit=r["credit"], matched_by=how, anchor_time=anchor),
                                   action="DO_NOT_RELEASE_GOODS_REPORT_1930", priority=_rs(short), money_key=f"order:{oid}:collect"))
            elif short < 0:
                Dd["verified"] += 0
                Dd["over"] += -short
        else:
            Dd["unverified"] += paise(o["amount"])
            out.append(Finding("recon", "UNVERIFIED_DIRECT_PAYMENT", oid, o["amount"], "critical",
                               f"Books record {inr(o['amount'])} received from {o.get('customer')} by {o.get('mode')}"
                               + (f" (ref {ref})" if ref else "") + ", but no such credit exists in the bank statement. "
                               "If the customer showed a payment screenshot, it may be fake.",
                               dict(ref=ref or None, mode=o.get("mode"), statement_as_of=L.get("bank_statement_as_of"), anchor_time=anchor),
                               action="DO_NOT_RELEASE_GOODS_REPORT_1930", priority=o["amount"], money_key=f"order:{oid}:collect"))

    # ═════ C: settlement batches vs bank credits ═════
    # the statement only covers [from, as_of]: settlements outside it can't be expected in it
    as_of = parse_ts(L.get("bank_statement_as_of"))
    stmt_from = parse_ts(L.get("bank_statement_from")) or (datetime.combine(min(bank_dates), datetime.min.time()).replace(tzinfo=IST) if bank_dates else None)

    def _in_period(b):
        t = parse_ts(b.get("settlement_date"))
        if t is None:
            return True
        t = t if t.tzinfo else t.replace(tzinfo=IST)
        return (not as_of or t <= as_of) and (not stmt_from or t >= stmt_from)
    batches = [b for b in all_batches if _in_period(b)]
    after_cutoff = [b for b in all_batches if b not in batches]
    comp = {str(b["cf_settlement_id"]): _composition(b, feed_by_id, ledger_ids) for b in batches}
    left = [Transaction("cashfree", (b.get("utr") or f"UTR{b['cf_settlement_id']}").upper(), dec2(b["amount_settled"]), "INR",
                        parse_date(b.get("settlement_date")), dict(b), n) for n, b in enumerate(batches)]
    right = [Transaction("bank", _ref(r["narration"]), dec2(r["credit"]), "INR", parse_date(r["date"]), dict(r), i)
             for i, r in credits if i not in used]
    # 1. same-amount matching: generic precision-first matcher (reference evidence, look-alike + contradiction guards)
    policy = L.get("match_policy") or MATCH_POLICY
    fallback = (as_of or datetime.now(IST)).date()
    l_items = [dict(id=n, amount=paise(t.amount), date=t.date or fallback, text=t.key) for n, t in enumerate(left)]
    r_items = [dict(id=t.line, amount=paise(t.amount), date=t.date or fallback, text=t.row["narration"]) for t in right]
    res = match_txns(l_items, r_items, window_days=MATCH_WINDOW_DAYS, min_fragment=MATCH_MIN_FRAGMENT, require_token=True,
                     use_unique=policy == "allow_amount_only", min_unique_amount=MATCH_MIN_AMOUNT_ONLY_PAISE)
    rmap = {t.line: t for t in right}
    pairs, groups, ml, mr = [], [], set(), set()
    for lids, rids, method, ev in res:
        if len(lids) == 1 and len(rids) == 1:
            lt, rt = left[lids[0]], rmap[rids[0]]
            refm = ref_evidence(lt.key, rt.row["narration"])[0]
            pairs.append((lt, rt, "exact_ref" if refm == "exact_ref" else (method if method != "amount+ref_fragment" else f"{method}:{refm or 'fragment'}")))
        else:
            groups.append(([left[i] for i in lids], [rmap[i] for i in rids], method))
        ml.update(lids); mr.update(rids)
    un_l = [t for n, t in enumerate(left) if n not in ml]
    un_r = [t for t in right if t.line not in mr]
    # 2. different amounts (short / excess credit) only on a verbatim or truncated reference that exactly one bank line
    #    carries: never on fuzzy similarity (on real bank data, amount-blind fuzzy pairing was wrong 471/471 times)
    for lt in sorted(un_l, key=lambda t: t.line):
        cands = [(rt, *ref_evidence(lt.key, rt.row["narration"])) for rt in un_r]
        cands = [c for c in cands if c[1] in ("exact_ref", "truncated_ref")]
        if len(cands) == 1:
            rt, how, _ = cands[0]; un_r.remove(rt); un_l.remove(lt); pairs.append((lt, rt, how))

    C = defaultdict(int)
    C["settled"] = sum(paise(t.amount) for t in left)
    credit_owner = {}  # bank row index -> ("settlement", key) | ("unexplained", finding) | ("duplicate", key) | ("direct", oid)
    for i, oid in direct_hits.items():
        credit_owner[i] = ("direct", oid)
    for lt, rt, how in pairs:
        sid = str(lt.row.get("cf_settlement_id")); C["credited"] += paise(rt.amount); credit_owner[rt.line] = ("settlement", lt.key)
        delta = paise(lt.amount) - paise(rt.amount)
        ev = dict(cf_settlement_id=sid, settled=float(lt.amount), credited=float(rt.amount), match=how, narration=rt.row["narration"],
                  settlement_date=lt.row.get("settlement_date"), anchor_time=lt.row.get("settlement_date"), batch=comp.get(sid))
        if delta > 0:
            C["short"] += delta
            out.append(Finding("recon", "SHORT_CREDIT", lt.key, _rs(delta), "high",
                               f"Settlement {lt.key} of {inr(lt.amount)} reached the bank as {inr(rt.amount)}: short by {inr(_rs(delta))}.",
                               ev, action="RAISE_WITH_BANK_OR_CASHFREE", priority=_rs(delta), money_key=f"settlement:{lt.key}"))
        elif delta < 0:
            C["excess"] += -delta
            out.append(Finding("recon", "EXCESS_CREDIT", lt.key, _rs(-delta), "medium",
                               f"Settlement {lt.key} of {inr(lt.amount)} reached the bank as {inr(rt.amount)}: {inr(_rs(-delta))} MORE than Cashfree settled. "
                               "Hold the extra in suspense; it may be reversed.",
                               ev, action="CONFIRM_WITH_BANK", priority=_rs(-delta) * 0.3, money_key=f"settlement:{lt.key}"))
        elif not how.endswith("exact_ref"):
            out.append(Finding("recon", "MATCHED_WITH_MANGLED_REF", lt.key, 0.0, "info",
                               f"Settlement {lt.key} matched on amount and date only (no reference evidence; policy {policy})."
                               if how.startswith("amount+date_unique") else
                               f"Settlement {lt.key} matched a bank credit whose reference was altered ({how}).",
                               dict(narration=rt.row["narration"], amount=float(rt.amount), match=how)))
    for gl, gr, how in groups:  # one bank line = several settlements, or the reverse: amounts sum exactly
        C["credited"] += sum(paise(t.amount) for t in gr)
        for rt in gr:
            credit_owner[rt.line] = ("settlement", gl[0].key)
        out.append(Finding("recon", "MATCHED_AS_GROUP", gl[0].key, 0.0, "info",
                           f"{len(gl)} settlement(s) matched {len(gr)} bank credit(s) as a group ({how}).",
                           dict(settlements=[t.key for t in gl], narrations=[t.row["narration"] for t in gr])))
    for lt in un_l:
        sid = str(lt.row.get("cf_settlement_id")); C["missing"] += paise(lt.amount)
        lookalikes = [rt for rt in un_r if paise(rt.amount) == paise(lt.amount) and lt.date and rt.date
                      and abs((lt.date - rt.date).days) <= MATCH_WINDOW_DAYS] if policy == "allow_amount_only" else []
        if lookalikes:  # a credit of this amount exists, but nothing proves which one: a review item, not a missing credit
            out.append(Finding("recon", "NEEDS_REVIEW", lt.key, float(lt.amount), "medium",
                               f"{inr(lt.amount)} ({lt.key}) has {len(lookalikes)} same-amount bank credit(s) nearby but no reference evidence "
                               "to choose one. Confirm the match by hand.",
                               dict(cf_settlement_id=sid, candidates=[r.row["narration"] for r in lookalikes[:5]],
                                    settlement_date=lt.row.get("settlement_date"), anchor_time=lt.row.get("settlement_date")),
                               action="CONFIRM_MATCH", priority=float(lt.amount) * 0.1, money_key=f"settlement:{lt.key}"))
            continue
        out.append(Finding("recon", "MISSING_CREDIT", lt.key, float(lt.amount), "critical",
                           f"Cashfree settled {inr(lt.amount)} (UTR {lt.key}) but no matching bank credit found.",
                           dict(cf_settlement_id=sid, settlement_date=lt.row.get("settlement_date"), anchor_time=lt.row.get("settlement_date"),
                                batch=comp.get(sid)),
                           action="RAISE_WITH_BANK_OR_CASHFREE", priority=float(lt.amount), money_key=f"settlement:{lt.key}"))
    # unmatched credits: a repeat of a matched settlement, a Cashfree credit matching nothing, or other income
    D = defaultdict(int); classified = {rt.line for _, rt, _ in pairs} | {rt.line for _, gr, _ in groups for rt in gr}
    for rt in un_r:
        dup_of = next((lt for lt, prt, _ in pairs if paise(rt.amount) in (paise(prt.amount), paise(lt.amount))
                       and ref_evidence(lt.key, rt.row["narration"])[0]), None)
        if dup_of is not None:
            D["duplicate"] += paise(rt.amount); classified.add(rt.line); credit_owner[rt.line] = ("duplicate", dup_of.key)
            out.append(Finding("recon", "DUPLICATE_CREDIT", dup_of.key, float(rt.amount), "medium",
                               f"Bank shows a second credit of {inr(rt.amount)} for settlement {dup_of.key}. Don't count it as revenue; it may be reversed.",
                               dict(narration=rt.row["narration"], date=rt.row["date"], anchor_time=rt.row["date"]),
                               action="CONFIRM_WITH_BANK", priority=float(rt.amount) * 0.3, money_key=f"bankrow:{rt.line}"))
        elif PG_RX.search(rt.row["narration"]):
            D["unexplained"] += paise(rt.amount); classified.add(rt.line)
            f = Finding("recon", "UNEXPLAINED_CASHFREE_CREDIT", rt.key or f"row{rt.line}", float(rt.amount), "high",
                        f"Bank shows a {inr(rt.amount)} credit naming Cashfree that matches no Cashfree settlement. Keep it in suspense "
                        "until the bank confirms it; unexplained credits get reversed (UCO Bank IMPS, Nov 2023).",
                        dict(narration=rt.row["narration"], date=rt.row["date"], anchor_time=rt.row["date"], settlements_checked=len(left)),
                        action="CONFIRM_WITH_BANK", priority=float(rt.amount) * 0.5, money_key=f"bankrow:{rt.line}")
            out.append(f); credit_owner[rt.line] = ("unexplained", f)
        else:
            D["other"] += paise(rt.amount); D["other_n"] += 1

    # ═════ R: bank debits that pull an earlier credit back ═════
    R = defaultdict(int)
    for i, r in enumerate(bank):
        if r["debit"] <= 0 or not REV_RX.search(r["narration"]):
            continue
        body = REV_RX.sub("", r["narration"], count=1).strip(" -/").upper()
        rref = _ref(body)
        # same reference (any amount: partial reversals exist) or the identical narration and amount
        target = next((j for j, c in credits if j in credit_owner and rref and _ref(c["narration"]) == rref), None)
        if target is None:
            target = next((j for j, c in credits if j in credit_owner and paise(c["credit"]) == paise(r["debit"])
                           and c["narration"].strip().upper() == body), None)
        amt = r["debit"]
        if target is None:
            if PG_RX.search(r["narration"]):
                R["unmatched"] += paise(amt)
                out.append(Finding("recon", "UNMATCHED_REVERSAL", rref or f"row{i}", amt, "high",
                                   f"Bank debited {inr(amt)} as a reversal naming Cashfree, but no matching earlier credit is in this statement.",
                                   dict(narration=r["narration"], date=r["date"], anchor_time=r["date"]),
                                   action="CONFIRM_WITH_BANK", priority=amt, money_key=f"bankrow:{i}"))
            continue
        kind, ref = credit_owner[target]
        R[kind] += paise(amt)
        if kind == "settlement":
            out.append(Finding("recon", "CREDIT_REVERSED", ref, amt, "critical",
                               f"The bank pulled back the {inr(amt)} credit for Cashfree settlement {ref} on {r['date']}. "
                               "The settlement no longer sits in your account: raise it with the bank and Cashfree.",
                               dict(narration=r["narration"], date=r["date"], anchor_time=r["date"]), action="RAISE_WITH_BANK_OR_CASHFREE",
                               priority=amt, money_key=f"settlement:{ref}:reversed"))
        elif kind == "unexplained":
            ref.severity, ref.kind = "medium", "CREDIT_REVERSED"
            ref.summary = (f"An unbacked {inr(amt)} credit naming Cashfree was pulled back by the bank on {r['date']} (UCO 2023 pattern). "
                           "Net effect zero; make sure it was never booked as revenue.")
            ref.evidence.update(reversal=r["narration"], reversed_on=r["date"]); ref.action = "CHECK_NOT_BOOKED"
        elif kind == "direct":
            out.append(Finding("recon", "CREDIT_REVERSED", ref, amt, "critical",
                               f"The {inr(amt)} payment recorded for {ref} was reversed by the bank on {r['date']}: the sale is unpaid again.",
                               dict(narration=r["narration"], date=r["date"], anchor_time=r["date"]), action="CHASE_OR_HOLD_DELIVERY",
                               priority=amt, money_key=f"order:{ref}:collect"))
        else:  # a duplicate credit pulled back: resolves itself
            out.append(Finding("recon", "CREDIT_REVERSED", ref, amt, "info",
                               f"The duplicate {inr(amt)} credit for settlement {ref} was reversed by the bank: resolved.",
                               dict(narration=r["narration"], date=r["date"])))

    # Cashfree's own batch must add up: per-order net in the recon feed == amount settled
    for b in batches:
        c = comp[str(b["cf_settlement_id"])]
        if c.get("available") and paise(c["net"]) != paise(b["amount_settled"]):
            d = paise(b["amount_settled"]) - paise(c["net"]); key = (b.get("utr") or f"UTR{b['cf_settlement_id']}").upper()
            out.append(Finding("recon", "BATCH_TOTAL_MISMATCH", key, _rs(abs(d)), "high",
                               f"Cashfree's settlement {key} says {inr(b['amount_settled'])}, but its own per-order lines add up to {inr(c['net'])}.",
                               dict(cf_settlement_id=b["cf_settlement_id"], batch=c, anchor_time=b.get("settlement_date")),
                               action="RAISE_WITH_CASHFREE", priority=_rs(abs(d)), money_key=f"settlement:{key}:composition"))

    # ═════ TIE-OUT (integer paise) ═════
    pg_total = sum(paise(rt.amount) for rt in right if PG_RX.search(rt.row["narration"]) or rt.line in classified)
    rev_total = sum(paise(r["debit"]) for r in bank if r["debit"] > 0 and REV_RX.search(r["narration"])
                    and (PG_RX.search(r["narration"]) or any(j in credit_owner and (_ref(c["narration"]) and
                         _ref(c["narration"]) == _ref(REV_RX.sub("", r["narration"], count=1)) or
                         (paise(c["credit"]) == paise(r["debit"]) and c["narration"].strip().upper() ==
                          REV_RX.sub("", r["narration"], count=1).strip(" -/").upper())) for j, c in credits)))
    sections = [
        _section("A. Merchant books vs Cashfree captured",
                 [("Books: orders paid via Cashfree", A["books_paid"]), ("less: shipped as PAID, never captured", -A["fbu"]),
                  ("add: captured, books show unpaid (incl. duplicate payments)", A["pnf"]),
                  ("add/less: captured differs from books (incl. double charges)", A["mismatch"]), ("= Cashfree captured", A["captured"])],
                 A["books_paid"] - A["fbu"] + A["pnf"] + A["mismatch"] - A["captured"]),
        _section("B. Cashfree captured vs settled",
                 [("Captured (orders in books)", B["captured"]), ("settled (gross)", B["settled_gross"]),
                  (f"in transit (within {SETTLEMENT_SLA_HOURS}h cycle, not yet due)", B["in_transit"]), ("overdue, not settled", B["overdue"]),
                  ("memo: fees deducted", B["fees"]), ("memo: GST on fees", B["gst"]), ("memo: net paid out", B["net"])],
                 B["captured"] - B["settled_gross"] - B["in_transit"] - B["overdue"],
                 note=f"fees + GST + net vs gross differs by {inr(_rs(B['settled_gross'] - B['fees'] - B['gst'] - B['net']))} (itemised by the leakage agent)"),
        _section("B2. Deductions inside settlements (orders in books)",
                 [("Deducted by Cashfree (net of reversals)", K["total"]), ("chargebacks / disputes", K["chargebacks"]),
                  ("refunds the books record", K["refunds_in_books"]), ("refunds missing from books", K["refunds_not_in_books"]),
                  ("other adjustments", K["adjustments"])],
                 K["total"] - K["chargebacks"] - K["refunds_in_books"] - K["refunds_not_in_books"] - K["adjustments"],
                 note=f"deductions on orders outside these books (other channels): {inr(_rs(K['other_channels']))}"),
        _section("C. Cashfree settled vs bank",
                 [("Settled in statement period", C["settled"]), ("credited by bank (matched)", C["credited"]),
                  ("add: short credits", C["short"]), ("less: excess credits", -C["excess"]), ("add: missing or unconfirmed credits", C["missing"])],
                 C["settled"] - (C["credited"] + C["short"] - C["excess"] + C["missing"])),
        _section("C2. Bank credits from Cashfree, fully classified",
                 [("Bank credits naming/matching Cashfree", pg_total), ("matched to a settlement", C["credited"]),
                  ("duplicate credits", D["duplicate"]), ("unexplained Cashfree credits", D["unexplained"])],
                 pg_total - (C["credited"] + D["duplicate"] + D["unexplained"]),
                 note=f"{D['other_n']} other credits ({inr(_rs(D['other']))}) are not from Cashfree or the books and are out of scope"),
        _section("C3. Reversal debits, fully classified",
                 [("Reversals of Cashfree/books credits", rev_total), ("settlement credits pulled back", R["settlement"]),
                  ("unexplained credits pulled back", R["unexplained"]), ("direct payments pulled back", R["direct"]),
                  ("duplicate credits pulled back", R["duplicate"]), ("reversals with no matching credit", R["unmatched"])],
                 rev_total - sum(R[k] for k in ("settlement", "unexplained", "direct", "duplicate", "unmatched"))),
        _section("D. Direct payments in books vs bank",
                 [("Books: paid directly (UPI to shop / bank transfer)", Dd["books"]), ("found in bank", Dd["verified"]),
                  ("short vs books (edited screenshot?)", Dd["short"]), ("not in bank at all", Dd["unverified"]),
                  ("memo: cash sales (not verifiable from bank)", Dd["cash"])],
                 Dd["books"] - Dd["verified"] - Dd["short"] - Dd["unverified"]),
    ]
    for s in sections:
        if not s["ok"]:
            out.append(Finding("recon", "TIEOUT_BREAK", s["title"].split(".")[0], abs(s["residual"]), "critical",
                               f"Tie-out {s['title']} is off by {inr(abs(s['residual']))}: some money is not explained by any finding. Do not sign off the books.",
                               dict(section=s), action="INVESTIGATE", priority=abs(s["residual"]) * 2, money_key=f"tieout:{s['title'].split('.')[0]}"))
    ctx["tieout"] = dict(ok=all(s["ok"] for s in sections), sections=sections, in_transit=in_transit,
                         statement_period=dict(start=stmt_from.isoformat() if stmt_from else None, end=as_of.isoformat() if as_of else None),
                         settlements_outside_statement_period=[(b.get("utr") or b["cf_settlement_id"], b["amount_settled"]) for b in after_cutoff])
    ctx["recon_pairs"] = ([dict(settlement=lt.row.get("cf_settlement_id"), bank_row=rt.line, how=how) for lt, rt, how in pairs]
                          + [dict(settlement=lt.row.get("cf_settlement_id"), bank_row=rt.line, how=how)
                             for gl, gr, how in groups for lt in gl for rt in gr])  # audit trail
    ctx["recon_stats"] = dict(settlements=len(left), bank_rows=len(bank), paired=len(pairs), other_credits=D["other_n"],
                              direct_sales=len(direct))
    return out
