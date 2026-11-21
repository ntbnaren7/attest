"""Money is integer paise everywhere. Floats never touch an amount."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


class AmountError(ValueError):
    pass


_LAKH = Decimal(100000)
_CRORE = Decimal(10000000)


def parse_rupees_to_paise(raw) -> int:
    """Parse an operator/ledger amount into integer paise.

    Accepts "12500", "12,500.50", "₹1,25,000", "Rs. 5000", "1.25 lakh", "2 cr".
    Rejects negatives, more than 2 decimal places, and empty or ambiguous text.
    """
    if raw is None:
        raise AmountError("amount is missing")
    if isinstance(raw, bool):
        raise AmountError("amount is not a number")
    if isinstance(raw, int):
        if raw < 0:
            raise AmountError("amount is negative")
        return raw * 100
    if isinstance(raw, float):
        # Floats are only accepted from provider JSON; round-trip through repr text.
        raw = repr(raw)
    s = str(raw).strip().lower()
    if not s:
        raise AmountError("amount is empty")
    s = s.replace("₹", "").replace("inr", "").replace("rs.", "").replace("rs", "").strip()
    multiplier = Decimal(1)
    m = re.fullmatch(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(lakh|lakhs|lac|l|crore|crores|cr)?", s)
    if not m:
        raise AmountError(f"cannot read amount {raw!r}")
    num, unit = m.group(1), m.group(2)
    if unit in ("lakh", "lakhs", "lac", "l"):
        multiplier = _LAKH
    elif unit in ("crore", "crores", "cr"):
        multiplier = _CRORE
    if "," in num:
        # Commas must be thousands-style (Indian or Western) grouping, never decimals.
        intpart = num.split(".")[0]
        if not re.fullmatch(r"[0-9]{1,3}(,[0-9]{2})*(,[0-9]{3})|[0-9]{1,3}(,[0-9]{3})+", intpart):
            raise AmountError(f"unusual digit grouping in {raw!r}")
        num = num.replace(",", "")
    try:
        value = Decimal(num) * multiplier
    except InvalidOperation as e:
        raise AmountError(f"cannot read amount {raw!r}") from e
    if value < 0:
        raise AmountError("amount is negative")
    paise = value * 100
    if paise != paise.to_integral_value():
        raise AmountError(f"amount {raw!r} has fractions of a paisa")
    return int(paise)


def provider_amount_to_paise(value) -> int:
    """Provider JSON amounts may arrive as number or string ("200.12")."""
    if value is None:
        raise AmountError("provider amount missing")
    return parse_rupees_to_paise(str(value) if not isinstance(value, float) else repr(value))


def paise_to_rupee_str(paise: int) -> str:
    """Decimal string for provider APIs, e.g. 1250050 -> '12500.50'."""
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    return f"{sign}{paise // 100}.{paise % 100:02d}"


def format_inr(paise: int) -> str:
    """Indian grouping for people: 12500000 -> '₹1,25,000'; keeps paise when non-zero."""
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    rupees, p = divmod(paise, 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{sign}₹{s}" + (f".{p:02d}" if p else "")
