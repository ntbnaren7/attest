from datetime import date

import pytest

from app.indic_numbers import corrected_amount, spoken_digits
from app.money import AmountError, format_inr, paise_to_rupee_str, parse_rupees_to_paise
from app.policy import SCORING_FEATURES, score
from app.signals import extract


@pytest.mark.parametrize("raw,paise", [("12500", 1250000), ("12,500.50", 1250050), ("₹1,25,000", 12500000),
                                       ("Rs. 5000", 500000), ("1.25 lakh", 12500000), ("3,300.50", 330050)])
def test_amount_parsing(raw, paise):
    assert parse_rupees_to_paise(raw) == paise


@pytest.mark.parametrize("raw", ["-500", "12,50,0", "100.505", "abc", ""])
def test_amount_rejects(raw):
    with pytest.raises(AmountError):
        parse_rupees_to_paise(raw)


def test_formatting():
    assert format_inr(12500000) == "₹1,25,000"
    assert format_inr(745050) == "₹7,450.50"
    assert paise_to_rupee_str(745050) == "7450.50"


# Research pack TC049-TC060 utterances
@pytest.mark.parametrize("text,paise", [
    ("நான் பன்னிரெண்டாயிரத்து ஐநூறு ரூபாய் கட்டிட்டேன்; ரசீது அனுப்புறேன்.", 1250000),
    ("मैंने अभी बारह हज़ार पाँच सौ रुपये नहीं दिए हैं।", 1250000),
    ("पचास हज़ार नहीं, पाँच हज़ार रुपये का लिंक भेजिए।", 500000),
    ("ஐயாயிரம் ரூபாய் ஐம்பது பைசா; ஐயாயிரத்து ஐம்பது ரூபாய் இல்லை.", 500050),
    ("राशि एक हज़ार पच्चीस रुपये पचास पैसे है, एक लाख नहीं।", 102550),
])
def test_spoken_amounts_and_corrections(text, paise):
    assert corrected_amount(text)[0] == paise


def test_ambiguous_amount_is_not_guessed():
    amt, cands = corrected_amount("एक लाख पच्चीस हज़ार में से पचहत्तर हज़ार दे दिए, पचास हज़ार बाकी हैं।")
    assert amt is None and len(cands) == 3


def test_spoken_digits_keep_leading_zeros_and_corrections():
    assert spoken_digits("INV one zero four இல்லை, INV one zero five-க்கு தான்")[-1] == "105"
    assert "0087" in spoken_digits("UTR के आख़िरी चार अंक zero zero eight seven हैं")


def test_negation_is_not_a_paid_claim():
    s = extract("मैंने अभी बारह हज़ार पाँच सौ रुपये नहीं दिए हैं।", date(2026, 11, 21))
    assert not s.paid_claim and s.negated_paid


def test_note_signals():
    today = date(2026, 11, 21)
    assert extract("branch mein de diya kal", today).paid_claim
    s = extract("salary 5th ko aayega tab de dunga", today)
    assert s.salary_day == 5 and s.promise and s.promised_date == "2026-12-05" and s.amount_paise is None
    assert extract("husband in hospital - abhi nahi de sakta", today).hardship
    assert extract("asked us: don't call this number again", today).opt_out


def test_scorer_rejects_non_allowlisted_features():
    with pytest.raises(AssertionError):
        score({"days_past_due": 3, "pincode": 600001}, 100, None, False, date(2026, 11, 21))
    assert set(SCORING_FEATURES).isdisjoint({"gender", "religion", "caste", "pincode", "language", "name", "area"})


def test_devanagari_payment_phrasing_is_a_paid_claim():
    for t in ("मैंने पेमेंट कर दिया है", "पैसे भेज दिए हैं", "भुगतान हो गया"):
        assert extract(t, date(2026, 11, 21)).paid_claim, t
