"""Turn free text (field notes, transcripts) into candidate claims.

Everything here is UNTRUSTED. Output is only ever recorded as a claim to verify; it never
changes a balance, never grants authority, and never selects a tool by itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .indic_numbers import corrected_amount

PAID = [
    r"\balready paid\b", r"\bpaid\b", r"\bde diya\b", r"\bde di\b", r"\bdiya hai\b", r"\bbhar diya\b", r"\bjama\b",
    r"\bpayment (?:ho gaya|done|kar diya|kiya)\b", r"दे दिया", r"दे दिए", r"चुका दिया", r"भर दिया", r"जमा कर",
    r"payment हो चुका", r"हो चुका है", r"भुगतान हो गया", r"भुगतान कर दिया", r"पेमेंट हो गया", r"पेमेंट कर दिया", r"पेमेंट कर दी", r"पैसे (भेज|दे) दिए", r"கட்டிட்டேன்", r"கட்டினேன்", r"ಕಟ್ಟಿದ್ದೇನೆ", r"ಕಟ್ಟಿದ್ದಾರೆ", r"ಪಾವತಿಸಿದ್ದೇನೆ", r"ಪಾವತಿ ಆಗಿದೆ", r"ಕೊಟ್ಟಿದ್ದೇನೆ", r"அனுப்பினேன்", r"பணம் கட்டி", r"payment பண்ணேன்",
]
NOT_PAID = [r"नहीं दि", r"नहीं दे", r"nahi diya", r"not paid", r"haven'?t paid", r"கட்டலை", r"கட்ட முடியாது", r"नहीं दूँगा", r"नहीं दूंगा", r"ಕಟ್ಟಿಲ್ಲ", r"ಕೊಟ್ಟಿಲ್ಲ"]
PROMISE = [
    r"\bwill pay\b", r"\bpay (?:by|on|tomorrow|next)\b", r"\bde dunga\b", r"\bde denge\b", r"\bkal\b", r"\btomorrow\b",
    r"दे दूँगा", r"दे दूंगा", r"दे देंगे", r"कल\b", r"நாளைக்கு", r"கட்டுறேன்", r"கட்டுவேன்", r"ನಾಳೆ", r"ಕಟ್ಟುತ್ತೇನೆ", r"ಕೊಡುತ್ತೇನೆ", r"\bpromise",
]
SALARY = [r"salary", r"सैलरी", r"तनख्वाह", r"vetan", r"சம்பளம்"]
DEDUCTION = [
    r"deduct", r"\breturn(?:ed)?\b", r"credit note", r"\bshort(?:age)?\b", r"damaged", r"wrong bill", r"galat",
    r"गलत", r"क्रेडिट नोट", r"काट", r"वापस", r"திருப்பி", r"கழி", r"ಕಡಿತ", r"ಹಿಂದಿರುಗಿಸಿ", r"tds", r"rate difference", r"dispute",
]
HARDSHIP = [
    r"lost (?:my |his |her )?job", r"job (?:chali|gayi|lost)", r"naukri (?:chali|gayi)", r"नौकरी (?:चली|गई)",
    r"hospital", r"अस्पताल", r"medical", r"illness", r"बीमार", r"death", r"passed away", r"गुज़र", r"cannot pay",
    r"can'?t pay", r"nahi de sakta", r"नहीं दे सकता", r"नहीं दे सकती", r"unemploy", r"வேலை போச்சு", r"மருத்துவமனை",
    r"flood", r"crop fail", r"फसल", r"ಆಸ್ಪತ್ರೆ", r"ಕೆಲಸ ಹೋಯಿತು", r"ಕಟ್ಟಲು ಆಗುವುದಿಲ್ಲ",
]
OPT_OUT = [
    r"don'?t call", r"do not call", r"stop calling", r"call (?:mat|na) kar", r"कॉल मत", r"फ़ोन मत", r"फोन मत",
    r"பண்ணாதீங்க", r"call பண்ணாதே", r"ಕರೆ ಮಾಡಬೇಡಿ", r"ಫೋನ್ ಮಾಡಬೇಡಿ",
]
REF = re.compile(
    r"\b(?:utr|ref(?:erence)?|order(?:_id| id)?|txn|cf)[\s:#\-]*(?:no\.?|number|id)?[\s:#\-]*([A-Za-z0-9_\-]{4,})",
    re.IGNORECASE)
ORDER_LIKE = re.compile(r"\b((?:CFORD|CFPay|CF|ORD|order|PL|LINK|rcv)[_\-][A-Za-z0-9_\-]{3,})\b", re.IGNORECASE)
DAY_OF_MONTH = re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:ko|tarikh|तारीख|को|date|of the month)\b", re.IGNORECASE)


def _any(patterns, text):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


@dataclass
class Signals:
    paid_claim: bool = False
    negated_paid: bool = False
    promise: bool = False
    salary_day: int | None = None
    deduction: bool = False
    hardship: bool = False
    opt_out: bool = False
    amount_paise: int | None = None
    paid_amount_paise: int | None = None
    deduction_amount_paise: int | None = None
    amount_ambiguous: bool = False
    reference: str | None = None
    promised_date: str | None = None
    matched: list[str] = field(default_factory=list)


def next_day_of_month(day: int, today: date) -> date | None:
    if not 1 <= day <= 31:
        return None
    y, m = today.year, today.month
    for _ in range(3):
        try:
            d = date(y, m, day)
            if d >= today:
                return d
        except ValueError:
            pass
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return None


def extract(text: str, today: date) -> Signals:
    s = Signals()
    if not text or not text.strip():
        return s
    t = text.strip()
    s.negated_paid = _any(NOT_PAID, t)
    s.paid_claim = _any(PAID, t) and not s.negated_paid
    s.hardship = _any(HARDSHIP, t)
    s.opt_out = _any(OPT_OUT, t)
    s.deduction = _any(DEDUCTION, t)
    salary = _any(SALARY, t)
    m = DAY_OF_MONTH.search(t)
    if salary and m:
        s.salary_day = int(m.group(1))
    s.promise = _any(PROMISE, t) and not s.paid_claim
    if s.promise:
        if re.search(r"\b(kal|tomorrow)\b|कल|நாளைக்கு|ನಾಳೆ", t, re.IGNORECASE):
            s.promised_date = (today + timedelta(days=1)).isoformat()
        elif m:
            d = next_day_of_month(int(m.group(1)), today)
            s.promised_date = d.isoformat() if d else None
        else:
            dm = re.search(r"\b(20\d\d-\d\d-\d\d)\b", t)
            s.promised_date = dm.group(1) if dm else None
    amt, cands = corrected_amount(t)
    # A day-of-month like "5th" is not money.
    if amt is not None and s.salary_day and amt == s.salary_day * 100:
        amt = None
    s.amount_paise = amt
    s.amount_ambiguous = amt is None and len(cands) > 1
    # Per-clause amounts: "paid 20000 by X, deducted 5000 for returns" -> paid 20000, deduction 5000.
    s.paid_amount_paise = amt if s.paid_claim else None
    s.deduction_amount_paise = amt if s.deduction else None
    if amt is None and len(cands) > 1:
        for clause in re.split(r"[,;।]|\band\b|\bbut\b|\bलेकिन\b|\bपर\b", t):
            camt, _ = corrected_amount(clause)
            if camt is None:
                continue
            if s.deduction and _any(DEDUCTION, clause):
                s.deduction_amount_paise = camt
            elif s.paid_claim and _any(PAID, clause):
                s.paid_amount_paise = camt
    r = ORDER_LIKE.search(t) or REF.search(t)
    if r:
        s.reference = r.group(1)
    for name in ("paid_claim", "promise", "deduction", "hardship", "opt_out"):
        if getattr(s, name):
            s.matched.append(name)
    if s.salary_day:
        s.matched.append(f"salary_day={s.salary_day}")
    return s
