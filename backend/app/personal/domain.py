"""Pure domain calculations for personal & recurring subscriptions: anchored dates, periods, currency."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP


def add_anchored_months(anchor_day: int, ref_date: date, months: int = 1) -> date:
    """Advance ref_date by `months` calendar months using anchored day-of-month clamping."""
    total_months = (ref_date.year * 12 + (ref_date.month - 1)) + months
    target_year = total_months // 12
    target_month = (total_months % 12) + 1
    max_day = calendar.monthrange(target_year, target_month)[1]
    return date(target_year, target_month, min(anchor_day, max_day))


def next_renewal_date(anchor_day: int, current_date: date, billing_interval: str) -> date | None:
    norm = "annual" if billing_interval == "yearly" else billing_interval
    if norm == "monthly":
        return add_anchored_months(anchor_day, current_date, 1)
    if norm == "quarterly":
        return add_anchored_months(anchor_day, current_date, 3)
    if norm == "annual":
        return add_anchored_months(anchor_day, current_date, 12)
    if norm == "weekly":
        return current_date + timedelta(days=7)
    if norm in ("one_time", "custom"):
        return None
    raise ValueError(f"Unsupported billing_interval: {billing_interval}")


def project_occurrences_in_range(
    first_renewal: date,
    billing_interval: str,
    anchor_day: int,
    range_start: date,
    range_end: date,
) -> list[date]:
    """Return occurrence dates falling inside half-open [range_start, range_end)."""
    if range_start >= range_end:
        return []
    norm = "annual" if billing_interval == "yearly" else billing_interval
    if norm in ("one_time", "custom"):
        return [first_renewal] if range_start <= first_renewal < range_end else []

    occurrences: list[date] = []
    cur = first_renewal
    # Walk backwards only when first_renewal is after range_end is impossible; if before range_start, advance forward
    steps = 0
    while cur < range_end and steps < 500:
        if cur >= range_start:
            occurrences.append(cur)
        nxt = next_renewal_date(anchor_day, cur, norm)
        if nxt is None or nxt <= cur:
            break
        cur = nxt
        steps += 1
    return occurrences


def parse_utc_instant(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_in_half_open_period(occurred_at: str | datetime, start: str | datetime, end: str | datetime) -> bool:
    """Return True iff start <= occurred_at < end."""
    occ = parse_utc_instant(occurred_at)
    s = parse_utc_instant(start)
    e = parse_utc_instant(end)
    return s <= occ < e


def monthly_equivalent_minor(price_minor: int, billing_interval: str, product_type: str = "recurring_subscription") -> int:
    norm = "annual" if billing_interval == "yearly" else billing_interval
    if product_type == "one_time_purchase" or norm in ("one_time", "custom"):
        return 0
    if norm == "monthly":
        return price_minor
    if norm == "quarterly":
        return int((Decimal(price_minor) / Decimal(3)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if norm == "annual":
        return int((Decimal(price_minor) / Decimal(12)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if norm == "weekly":
        return int((Decimal(price_minor) * Decimal(52) / Decimal(12)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    raise ValueError(f"Unsupported billing_interval: {billing_interval}")


def annual_equivalent_minor(price_minor: int, billing_interval: str, product_type: str = "recurring_subscription") -> int:
    norm = "annual" if billing_interval == "yearly" else billing_interval
    if product_type == "one_time_purchase" or norm in ("one_time", "custom"):
        return 0
    if norm == "annual":
        return price_minor
    if norm == "quarterly":
        return price_minor * 4
    if norm == "monthly":
        return price_minor * 12
    if norm == "weekly":
        return price_minor * 52
    raise ValueError(f"Unsupported billing_interval: {billing_interval}")
