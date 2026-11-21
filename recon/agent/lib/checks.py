"""Deterministic checks shared by agents: payee name matching, sanctions screening, IFSC registry.
All benchmarked in research/real_data/RESULTS.md."""
import json
import os
import re
from functools import lru_cache

import pandas as pd
from rapidfuzz import fuzz, process

from ..config import DATA, SANCTIONS_THRESHOLD

# ───────────────────────── guarded name match (2/1881 false accepts vs 146 for naive RapidFuzz) ─────────────────────────
TITLES = {"MR", "MRS", "MS", "DR", "SHRI", "SMT", "KUM", "MESSRS"}
CO_EXPAND = {"PVT": "PRIVATE", "LTD": "LIMITED", "CO": "COMPANY", "CORPN": "CORPORATION", "CORP": "CORPORATION", "&": "AND", "KR": "KUMAR"}
CO_GENERIC = {"PRIVATE", "LIMITED", "COMPANY", "CORPORATION", "INDIA", "AND", "THE", "LLP", "ENTERPRISES", "SOLUTIONS",
              "INDUSTRIES", "SERVICES", "TRADERS", "WORKS", "LOGISTICS", "HARDWARE", "ELECTRICALS", "STORAGE", "DAIRY"}


def norm_tokens(s):
    toks = re.sub(r"[^A-Z0-9& ]", " ", (s or "").upper().replace("M/S", " ")).split()
    return [CO_EXPAND.get(t, t) for t in toks if t not in TITLES]


def name_match(a, b):
    """-> ("MATCH" | "PARTIAL" | "NO_MATCH", score). Persons: surname must agree fully; given names full or initial.
    Companies: every distinctive token must agree."""
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb:
        return "NO_MATCH", 0
    if any(t in CO_GENERIC for t in ta + tb):
        da, db = [t for t in ta if t not in CO_GENERIC], [t for t in tb if t not in CO_GENERIC]
        if not da or not db:
            return "NO_MATCH", 0
        hit = all(any(fuzz.ratio(x, y) >= 90 for y in db) for x in da) and all(any(fuzz.ratio(x, y) >= 90 for y in da) for x in db)
        return ("MATCH", 100) if hit else ("NO_MATCH", 0)

    def tok_ok(x, y):
        if len(x) == 1 or len(y) == 1:
            return x[0] == y[0], "initial"
        return fuzz.ratio(x, y) >= 88, "full"
    best = ("NO_MATCH", 0)
    for tb2 in (tb, tb[::-1], tb[1:] + tb[:1], tb[-1:] + tb[:-1]):
        if len(ta) != len(tb2):
            short, long_ = (ta, tb2) if len(ta) < len(tb2) else (tb2, ta)
            if len(short) < 2:
                continue
            cand, s2 = [long_[0], long_[-1]], [short[0], short[-1]]
        else:
            cand, s2 = tb2, ta
        res = [tok_ok(x, y) for x, y in zip(s2, cand)]
        if all(ok for ok, _ in res) and res[-1][1] == "full":
            kind = "PARTIAL" if any(k == "initial" for _, k in res) or len(ta) != len(tb) else "MATCH"
            score = 100 if kind == "MATCH" else 80
            if score > best[1]:
                best = (kind, score)
    return best


# ───────────────────────── sanctions (OpenSanctions: India MHA banned + UN SC; CC-BY-NC, demo use) ─────────────────────────
@lru_cache(maxsize=1)
def _sanction_names():
    names = []
    for f in ("in_mha_banned", "un_sc_sanctions"):
        s = pd.read_csv(os.path.join(DATA, f + ".csv"), dtype=str).fillna("")
        for r in s.itertuples():
            for n in [r.name] + [a for a in r.aliases.split(";") if a]:
                if len(n) > 4:
                    names.append((n.upper(), r.name, f, r.sanctions[:120]))
    return names


def sanctions_screen(name, threshold=SANCTIONS_THRESHOLD):
    names = _sanction_names()
    best = process.extractOne((name or "").upper(), [n[0] for n in names], scorer=fuzz.token_sort_ratio)
    if best and best[1] >= threshold:
        hit = names[best[2]]
        return dict(hit=True, score=round(best[1], 1), matched=hit[0], listed_as=hit[1], list=hit[2], basis=hit[3])
    return dict(hit=False, score=round(best[1], 1) if best else 0)


# ───────────────────────── IFSC registry (razorpay/ifsc v2.0.62, 183k codes, MIT) ─────────────────────────
@lru_cache(maxsize=1)
def _ifsc():
    return set(json.load(open(os.path.join(DATA, "IFSC-list.json"))))


def ifsc_valid(code):
    return bool(code) and code.upper() in _ifsc()
