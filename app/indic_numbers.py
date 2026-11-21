"""Rule-based reader for spoken amounts in Hindi, Tamil, Hinglish and English.

This is the deterministic fallback and cross-check for the Sarvam model's extraction. It
handles the forms in the research test pack; anything it cannot read becomes a clarifying
question rather than a guess.

Corrections such as "पचास हज़ार नहीं, पाँच हज़ार" or "5050 இல்லை" drop the negated amount.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# value, kind: 'n' number, 'm' multiplier, 'p' paise marker, 'r' rupee marker
HI = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9,
    "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15, "सोलह": 16,
    "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "पच्चीस": 25, "तीस": 30, "पैंतीस": 35, "चालीस": 40,
    "पैंतालीस": 45, "पचास": 50, "साठ": 60, "सत्तर": 70, "पचहत्तर": 75, "अस्सी": 80, "नब्बे": 90,
    "डेढ़": 1.5, "ढाई": 2.5,
}
HI_ROMAN = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "che": 6, "saat": 7,
    "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "baarah": 12, "pandrah": 15, "bees": 20,
    "pachees": 25, "pachchis": 25, "tees": 30, "chalis": 40, "pachas": 50, "pachaas": 50, "saath": 60,
    "sattar": 70, "pachattar": 75, "assi": 80, "nabbe": 90, "dedh": 1.5, "dhai": 2.5,
}
EN = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
MULT = {
    "सौ": 100, "sau": 100, "hundred": 100, "हज़ार": 1000, "हजार": 1000, "hazaar": 1000, "hazar": 1000,
    "hajar": 1000, "thousand": 1000, "k": 1000, "लाख": 100000, "lakh": 100000, "lac": 100000, "lakhs": 100000,
    "करोड़": 10000000, "crore": 10000000,
}
# Tamil words are agglutinative; list the forms we read whole. 'த்து' endings continue a number.
TA = {
    "ஒன்று": 1, "ஒரு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5, "அஞ்சு": 5,
    "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10, "பன்னிரண்டு": 12, "பதினைந்து": 15,
    "இருபது": 20, "இருபத்தைந்து": 25, "முப்பது": 30, "நாற்பது": 40, "ஐம்பது": 50, "எழுபத்தைந்து": 75,
    "நூறு": 100, "இருநூறு": 200, "முந்நூறு": 300, "ஐநூறு": 500, "ஆயிரம்": 1000, "ஆயிரத்து": 1000,
    "இரண்டாயிரம்": 2000, "இரண்டாயிரத்து": 2000, "ரெண்டாயிரம்": 2000, "மூவாயிரம்": 3000,
    "ஐயாயிரம்": 5000, "ஐயாயிரத்து": 5000, "பத்தாயிரம்": 10000, "பத்தாயிரத்து": 10000,
    "பன்னிரெண்டாயிரம்": 12000, "பன்னிரெண்டாயிரத்து": 12000, "பன்னிரண்டாயிரத்து": 12000,
    "பன்னிரண்டாயிரம்": 12000, "இருபத்தைந்தாயிரம்": 25000, "ஐம்பதாயிரம்": 50000,
}
KN = {
    "ಒಂದು": 1, "ಎರಡು": 2, "ಮೂರು": 3, "ನಾಲ್ಕು": 4, "ಐದು": 5, "ಆರು": 6, "ಏಳು": 7, "ಎಂಟು": 8, "ಒಂಬತ್ತು": 9,
    "ಹತ್ತು": 10, "ಹನ್ನೆರಡು": 12, "ಇಪ್ಪತ್ತು": 20, "ಇಪ್ಪತ್ತೈದು": 25, "ಮೂವತ್ತು": 30, "ನಲವತ್ತು": 40, "ಐವತ್ತು": 50,
    "ಎಪ್ಪತ್ತೈದು": 75, "ಇನ್ನೂರು": 200, "ಮುನ್ನೂರು": 300, "ಐನೂರು": 500,
}
KN_MULT = {"ನೂರು": 100, "ಸಾವಿರ": 1000, "ಸಾವಿರದ": 1000, "ಲಕ್ಷ": 100000, "ಲಕ್ಷದ": 100000}
TA_MULT = {"லட்சம்": 100000, "லட்சத்து": 100000, "ஆயிரம்": 1000}
PAISE = {"पैसे": 1, "पैसा": 1, "paise": 1, "paisa": 1, "பைசா": 1, "ಪೈಸೆ": 1}
RUPEE = {"रुपये": 1, "रुपए": 1, "रुपया": 1, "rupees": 1, "rupaye": 1, "rs": 1, "₹": 1, "ரூபாய்": 1, "rupay": 1, "ರೂಪಾಯಿ": 1}
NEG = {"नहीं", "नही", "nahi", "nahin", "not", "இல்லை", "illa", "illai", "ಅಲ್ಲ", "ಇಲ್ಲ"}


@dataclass
class SpokenAmount:
    paise: int
    start: int
    end: int
    negated: bool = False


def _tokens(text: str) -> list[str]:
    text = text.replace("₹", " ₹ ")
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 12,500 / 1,25,000 are one number
    text = re.sub(r"(?<=\d)\.(?!\d)", " ", text)
    return [t for t in re.split(r"[\s,;।!?()\"']+|\.(?!\d)", text) if t]


def _num_value(tok: str):
    low = tok.lower()
    if re.fullmatch(r"\d+(\.\d+)?", tok):
        return float(tok) if "." in tok else int(tok), "n"
    for table in (HI, HI_ROMAN, EN):
        if low in table:
            return table[low], "n"
    if tok in TA:
        v = TA[tok]
        return v, "n"
    if low in MULT:
        return MULT[low], "m"
    if tok in TA_MULT:
        return TA_MULT[tok], "m"
    if tok in KN:
        return KN[tok], "n"
    if tok in KN_MULT:
        return KN_MULT[tok], "m"
    return None, None


def find_amounts(text: str) -> list[SpokenAmount]:
    """Return every amount phrase with its token span; negated ones are flagged."""
    toks = _tokens(text)
    out: list[SpokenAmount] = []
    i = 0
    while i < len(toks):
        v, kind = _num_value(toks[i])
        if v is None:
            i += 1
            continue
        start = i
        total = 0.0
        current = 0.0
        paise_part = 0
        saw_rupee_marker = False
        while i < len(toks):
            tok = toks[i]
            low = tok.lower()
            v, kind = _num_value(tok)
            if kind == "n":
                # Tamil whole-word thousands (e.g. ஐயாயிரத்து) add directly.
                if tok in TA and TA[tok] >= 1000:
                    total += TA[tok]
                elif tok in TA and TA[tok] >= 100 and current == 0:
                    total += TA[tok]
                else:
                    current += v
                i += 1
            elif kind == "m":
                current = (current or 1) * v
                if v >= 1000:
                    total += current
                    current = 0
                i += 1
            elif low in RUPEE or tok in RUPEE:
                saw_rupee_marker = True
                i += 1
                # Paise may follow: "... रुपये पचास पैसे"
                j = i
                pv = 0
                while j < len(toks):
                    vv, kk = _num_value(toks[j])
                    if kk == "n" and not (toks[j] in TA and TA[toks[j]] >= 100):
                        pv += vv
                        j += 1
                    else:
                        break
                if j < len(toks) and (toks[j].lower() in PAISE or toks[j] in PAISE) and j > i:
                    paise_part = int(pv)
                    i = j + 1
                break
            else:
                break
        rupees = total + current
        if rupees == 0 and paise_part == 0:
            i = max(i, start + 1)
            continue
        if rupees != int(rupees) and (rupees * 100) != int(rupees * 100):
            i = max(i, start + 1)
            continue
        amt = SpokenAmount(paise=int(round(rupees * 100)) + paise_part, start=start, end=i)
        # Negation directly after the phrase ("... नहीं", "... இல்லை") marks it as rejected.
        for k in range(i, min(i + 2, len(toks))):
            if toks[k].lower() in NEG or toks[k] in NEG:
                amt.negated = True
        out.append(amt)
        if not saw_rupee_marker and i == start:
            i += 1
    return out


def corrected_amount(text: str) -> tuple[int | None, list[int]]:
    """The amount the speaker means after corrections, plus every distinct candidate.

    Returns (None, candidates) when the meaning is ambiguous: the caller must ask.
    """
    amts = find_amounts(text)
    kept = [a.paise for a in amts if not a.negated]
    if not kept:
        # "12,500 रुपये नहीं दिए" negates the act, not the number: nothing replaced it.
        kept = [a.paise for a in amts]
    all_vals = sorted({a.paise for a in amts})
    distinct = sorted(set(kept))
    if len(distinct) == 1:
        return distinct[0], all_vals
    return None, all_vals


DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4",
    "पाँच": "5", "पांच": "5", "छह": "6", "सात": "7", "आठ": "8", "नौ": "9",
}


def spoken_digits(text: str) -> list[str]:
    """Collapse runs like 'one zero four' or 'zero zero eight seven' into '104' / '0087' (leading zeros kept)."""
    toks = re.split(r"[\s,;।.!?\-]+", text)
    runs, cur = [], ""
    for t in toks:
        low = t.lower().strip("-")
        base = re.sub(r"(-?க்கு|-?ஆ|-?ஐ)$", "", low)
        if base in DIGIT_WORDS:
            cur += DIGIT_WORDS[base]
        elif re.fullmatch(r"\d+", base):
            cur += base
        else:
            if cur:
                runs.append(cur)
            cur = ""
    if cur:
        runs.append(cur)
    return runs
