"""Date/time parsing shared by the agents. Cashfree timestamps are IST; payout ones carry no offset."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

IST = timezone(timedelta(hours=5, minutes=30))


def parse_ts(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def parse_date(s):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def age_minutes(added_on):
    """Minutes since a Cashfree payout timestamp such as 2026-11-20T10:20:39 (IST, no offset)."""
    try:
        t = datetime.fromisoformat(added_on).replace(tzinfo=IST)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def dec2(x):
    """Exact 2-decimal money value for the matcher (never float)."""
    return Decimal(str(x)).quantize(Decimal("0.01"))
