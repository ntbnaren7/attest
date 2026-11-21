"""Money helpers that agree with Cashfree to the paisa.

Verified on live sandbox settlements (seed run 20261120_094955): Cashfree derives fee and GST with float
round(x, 2). Python's round() reproduced 21/21 fees and GSTs; strict Decimal half-up missed 2/21 GSTs.
So: *derive* expected values the way Cashfree does, *compare* in integer paise.
"""


def cf_round(x):
    return round(float(x), 2)


def paise(x):
    return int(round(float(x) * 100))


def same(a, b, tol=0.0):
    return abs(paise(a) - paise(b)) <= int(round(tol * 100))


def num(x):
    """Parse a money cell from any real export: None, '', 'NA', '-', '1,234.50' -> float (missing = 0.0)."""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("₹", "")
    if s.upper() in ("", "NA", "N/A", "-", "NULL", "NONE"):
        return 0.0
    return float(s)


def inr(x):
    return f"₹{float(x):,.2f}"
