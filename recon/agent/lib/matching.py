"""Generic, precision-first transaction matcher (any currency, any bank / ledger reference scheme).

Used by the reconciliation engine for Cashfree settlement <-> bank credit matching, and validated on real bank data
(BenchRec, ICAIF 2023). Design rule: a wrong match is worse than no match. Anything ambiguous is left unmatched for
a human (BenchRec's operating bar is 99.8% match precision).

Items: dict(id, amount (signed, paise int), date (datetime.date), text (references + description), key (optional
partition, e.g. currency+account)).

Stages:
  1. one-to-one, same amount, |date diff| <= window, evidence = shared reference fragment
  2. one-to-one, same amount, no textual evidence: only if the pair is mutually unique (no look-alikes) on the same day
  3. grouped: one item = sum of several on the same day that share a reference fragment (n:1 and 1:n)
Look-alikes (candidates that are interchangeable: same amount, date and text) are treated as one; candidates that
differ in text but score the same are ambiguous -> abstain.
"""
import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations

_WS = re.compile(r"[^A-Z0-9]")
_DIGITS = re.compile(r"\d")


def norm(text):
    return _WS.sub("", (text or "").upper())


def fragment(a, b):
    """Longest shared alphanumeric fragment of two normalised texts (only fragments with >= 5 digits count)."""
    if not a or not b:
        return ""
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    s = a[m.a:m.a + m.size]
    if len(_DIGITS.findall(s)) < 5 or len(set(s)) < 4 or max(s.count(c) for c in set(s)) > len(s) / 2:
        return ""  # padding / masked filler such as '266666666' is not a reference
    return s


_TOK = re.compile(r"[A-Z0-9]{7,}")


def ref_tokens(text):
    """Reference-like tokens: >= 7 alphanumerics, >= 5 digits, not padding/filler."""
    out = set()
    for t in _TOK.findall((text or "").upper()):
        if len(_DIGITS.findall(t)) >= 5 and len(set(t)) >= 4 and max(t.count(c) for c in set(t)) <= len(t) / 2:
            out.add(t)
    return out


def _names(items_a, items_b, window_days):
    """For each item in items_b: ids of items_a whose reference token appears verbatim inside its text."""
    index = defaultdict(list)
    for a in items_a:
        for t in ref_tokens(a.get("text")):
            index[t].append(a)
    lengths = sorted({len(t) for t in index})
    out = defaultdict(set)
    for b in items_b:
        n = b["_n"]
        for L in lengths:
            for i in range(len(n) - L + 1):
                for a in index.get(n[i:i + L], ()):
                    if a.get("key") == b.get("key") and abs((a["date"] - b["date"]).days) <= window_days:
                        out[b["id"]].add(a["id"])
    return out


def _sig(it):
    return (it["amount"], it["date"], it["_n"])


def match(left, right, window_days=3, min_fragment=7, unique_same_day_only=True, max_group=6, use_unique=True, min_unique_amount=0, require_token=False, net_internal=False):
    """Returns list of (left_ids tuple, right_ids tuple, method, evidence)."""
    for it in left + right:
        it["_n"] = norm(it.get("text"))
    offsets = []
    if net_internal:  # an entry cancelled by an equal-and-opposite entry on the SAME side never meets the other side
        for side in (left, right):
            pool = defaultdict(list)
            for it in side:
                pool[(it.get("key"), abs(it["amount"]))].append(it)
            for (_, amt), grp in pool.items():
                pos = [x for x in grp if x["amount"] > 0]; neg = [x for x in grp if x["amount"] < 0]
                if not pos or not neg:
                    continue
                for x in pos:
                    tx = ref_tokens(x.get("text"))
                    mates = [y for y in neg if not y.get("_off") and abs((x["date"] - y["date"]).days) <= window_days
                             and (tx & ref_tokens(y.get("text")) or any(t in y["_n"] for t in tx))]
                    if len(mates) == 1 and not x.get("_off"):
                        x["_off"] = mates[0]["_off"] = True
                        offsets.append((x["id"], mates[0]["id"]))
        left = [x for x in left if not x.get("_off")]; right = [x for x in right if not x.get("_off")]
    r_names = _names(left, right, window_days)   # bank line text names these ledger ids
    l_names = _names(right, left, window_days)   # ledger line text names these bank ids
    named_by_r = defaultdict(set); named_by_l = defaultdict(set)
    for rid, lids in r_names.items():
        for lid in lids:
            named_by_r[lid].add(rid)
    for lid, rids in l_names.items():
        for rid in rids:
            named_by_l[rid].add(lid)

    def contradicted(l, r):
        """Either side's text names a DIFFERENT counterpart: an amount+date match would contradict the references."""
        return bool((r_names.get(r["id"], set()) - {l["id"]}) or (l_names.get(l["id"], set()) - {r["id"]})
                    or (named_by_r.get(l["id"], set()) - {r["id"]}) or (named_by_l.get(r["id"], set()) - {l["id"]}))
    by_amt = defaultdict(list)
    for l in left:
        by_amt[(l.get("key"), l["amount"])].append(l)
    edges = []  # (strength, date_diff, l, r, frag)
    cand_r = defaultdict(list); cand_l = defaultdict(list)
    for r in right:
        for l in by_amt.get((r.get("key"), r["amount"]), []):
            dd = abs((l["date"] - r["date"]).days)
            if dd > window_days:
                continue
            f = fragment(l["_n"], r["_n"])
            cand_r[r["id"]].append(l); cand_l[l["id"]].append(r)
            edges.append((len(f) if len(f) >= min_fragment else 0, dd, l, r, f))
    if require_token:  # evidence must be a whole reference token of one side found verbatim in the other
        edges = [(st if (l["id"] in r_names.get(r["id"], ()) or r["id"] in l_names.get(l["id"], ())) else 0, dd, l, r, f)
                 for st, dd, l, r, f in edges]
    used_l, used_r, out = set(), set(), []

    def distinct(items):
        return {_sig(x) for x in items}

    # 1. evidence-backed one-to-one: strongest evidence first, nearest date first
    edges.sort(key=lambda e: (-e[0], e[1]))
    for strength, dd, l, r, f in edges:
        if not strength or l["id"] in used_l or r["id"] in used_r:
            continue
        # a rival only matters if it could change WHICH entry gets matched: one that competes solely for identical
        # twins (same amount, date and text) of this entry is not ambiguity, the twins are interchangeable
        rivals_l = [x for x in cand_r[r["id"]] if x["id"] not in used_l and x is not l and _sig(x) != _sig(l)
                    and len(fragment(x["_n"], r["_n"])) >= strength]
        rivals_r = [x for x in cand_l[l["id"]] if x["id"] not in used_r and x is not r and _sig(x) != _sig(r)
                    and len(fragment(l["_n"], x["_n"])) >= strength
                    and any(_sig(y) != _sig(l) and y["id"] not in used_l for y in cand_r[x["id"]])]
        if rivals_l or rivals_r:
            continue  # a different-looking candidate is just as good: ambiguous
        used_l.add(l["id"]); used_r.add(r["id"])
        out.append(((l["id"],), (r["id"],), "amount+ref_fragment", dict(fragment=f, date_diff=dd)))
    # 2. no textual evidence: accept only when there is exactly one kind of candidate on both sides
    for strength, dd, l, r, f in sorted(edges, key=lambda e: e[1]):
        if not use_unique or l["id"] in used_l or r["id"] in used_r or (unique_same_day_only and dd):
            continue
        cl = [x for x in cand_r[r["id"]] if x["id"] not in used_l]
        cr = [x for x in cand_l[l["id"]] if x["id"] not in used_r]
        if abs(l["amount"]) < min_unique_amount:
            continue  # small amounts repeat by coincidence: uniqueness is not evidence
        if len(distinct(cl)) == 1 and len(distinct(cr)) == 1 and not contradicted(l, r):
            used_l.add(l["id"]); used_r.add(r["id"])
            out.append(((l["id"],), (r["id"],), "amount+date_unique", dict(date_diff=dd)))
    # 3. grouped: n:1 and 1:n on the same day, members sharing a reference fragment with the single side
    def groups(singles, many, used_s, used_m, flip):
        pool = defaultdict(list)
        for m in many:
            if m["id"] not in used_m:
                pool[(m.get("key"), m["date"])].append(m)
        for s in singles:
            if s["id"] in used_s:
                continue
            mem = [m for m in pool.get((s.get("key"), s["date"]), []) if m["id"] not in used_m
                   and len(fragment(m["_n"], s["_n"])) >= min_fragment and (m["amount"] > 0) == (s["amount"] > 0)]
            if len(mem) < 2 or len(mem) > 14:
                continue
            hits = []
            for k in range(2, min(max_group, len(mem)) + 1):
                for combo in combinations(mem, k):
                    if sum(c["amount"] for c in combo) == s["amount"]:
                        hits.append(combo)
                        if len(hits) > 1:
                            break
                if len(hits) > 1:
                    break
            if len(hits) == 1:
                combo = hits[0]
                used_s.add(s["id"]); used_m.update(c["id"] for c in combo)
                ids_m, ids_s = tuple(c["id"] for c in combo), (s["id"],)
                out.append(((ids_s, ids_m) if not flip else (ids_m, ids_s)) + ("grouped_sum+ref_fragment", dict(size=len(combo))))
    groups(right, left, used_r, used_l, flip=True)    # one bank line = several ledger lines
    groups(left, right, used_l, used_r, flip=False)   # one ledger line = several bank lines
    return out
