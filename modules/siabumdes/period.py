"""Accounting-period string parsing shared by period-close and the
recording-lock check.

Both BUMDes (Pusat) and unit usaha (UU01..UU06) close monthly, using the
single "YYYY-MM" shape.
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta

_PERIOD_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def period_kind(period: str) -> str:
    """Classify a period string. Only 'month' ("YYYY-MM") is supported;
    raises ValueError otherwise."""
    if _PERIOD_MONTH_RE.match(period or ""):
        return "month"
    raise ValueError("Format period harus YYYY-MM")


def period_range(period: str) -> tuple[date, date]:
    """Return the (start, end) dates covered by a "YYYY-MM" period string."""
    period_kind(period)
    year, month = int(period[:4]), int(period[5:7])
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def assert_period_kind_matches_group(period: str, group_code: str) -> str:
    """Both BUMDES and unit usaha close monthly. Returns the period's kind
    on success ("month"); raises ValueError with a user-facing message
    otherwise."""
    return period_kind(period)


def seed_period_buckets(
    monthly: dict[str, dict[str, float]], start: date, end: date, bucket_len: int
) -> None:
    """Pre-fill every bucket in [start, end] with a zero-value row.

    Matches /api/public/summary's behavior of always returning a full,
    contiguous set of periods (e.g. all 12 months of a year) rather than
    only the periods that happen to contain a transaction -- otherwise a
    trend chart for the current year silently stops at the last month with
    data instead of showing the full year with trailing zeros.
    """
    if bucket_len == 10:  # daily buckets, key = "YYYY-MM-DD"
        cur = start
        while cur <= end:
            key = cur.isoformat()
            monthly.setdefault(key, {"month": key, "pendapatan": 0.0, "beban": 0.0})
            cur += timedelta(days=1)
    else:  # monthly buckets, key = "YYYY-MM"
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            key = f"{y}-{m:02d}"
            monthly.setdefault(key, {"month": key, "pendapatan": 0.0, "beban": 0.0})
            m += 1
            if m > 12:
                m = 1
                y += 1
