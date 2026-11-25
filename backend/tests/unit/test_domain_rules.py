"""Unit tests for domain math: anchored dates, periods, currency segregation (T009-T015)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.personal.domain import (
    annual_equivalent_minor,
    is_in_half_open_period,
    monthly_equivalent_minor,
    next_renewal_date,
)


def test_t009_t011_t013_spending_equivalents():
    # T009: $20 monthly + $12 monthly = $32.00 (3200 minor)
    assert monthly_equivalent_minor(2000, "monthly") + monthly_equivalent_minor(1200, "monthly") == 3200
    # T011: one-time purchase $60 excluded from recurring monthly equivalent
    assert monthly_equivalent_minor(6000, "one_time", "one_time_purchase") == 0
    assert annual_equivalent_minor(6000, "one_time", "one_time_purchase") == 0
    # T013: Annual $240 -> Monthly $20, Annual $240
    assert monthly_equivalent_minor(24000, "annual") == 2000
    assert annual_equivalent_minor(24000, "annual") == 24000


def test_t014_anchored_calendar_month_jan31_non_leap_and_leap():
    # Non-leap 2027: Jan 31 -> Feb 28 -> Mar 31
    d1 = date(2027, 1, 31)
    d2 = next_renewal_date(31, d1, "monthly")
    assert d2 == date(2027, 2, 28)
    d3 = next_renewal_date(31, d2, "monthly")
    assert d3 == date(2027, 3, 31)

    # Leap 2024: Jan 31 -> Feb 29 -> Mar 31
    l1 = date(2024, 1, 31)
    l2 = next_renewal_date(31, l1, "monthly")
    assert l2 == date(2024, 2, 29)
    l3 = next_renewal_date(31, l2, "monthly")
    assert l3 == date(2024, 3, 31)


def test_t015_half_open_period_boundary():
    p1_start = "2026-11-01T00:00:00Z"
    p1_end = "2026-12-01T00:00:00Z"
    p2_start = "2026-12-01T00:00:00Z"
    p2_end = "2027-01-01T00:00:00Z"

    boundary = datetime(2026, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert is_in_half_open_period(boundary, p1_start, p1_end) is False
    assert is_in_half_open_period(boundary, p2_start, p2_end) is True
