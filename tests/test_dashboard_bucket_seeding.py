"""seed_period_buckets() is what fixes the Dashboard trend chart silently
stopping at the last month with a transaction, instead of showing the full
requested period (e.g. all 12 months of the current year) with trailing
zeros -- matching /api/public/summary's Landing page behavior."""
from __future__ import annotations

from datetime import date

from modules.siabumdes.period import seed_period_buckets


def test_seed_monthly_buckets_fills_full_year_even_with_no_transactions_yet():
    monthly: dict[str, dict[str, float]] = {}
    seed_period_buckets(monthly, date(2026, 1, 1), date(2026, 12, 31), bucket_len=7)

    keys = sorted(monthly)
    assert keys == [f"2026-{m:02d}" for m in range(1, 13)]
    assert all(row == {"month": key, "pendapatan": 0.0, "beban": 0.0} for key, row in monthly.items())


def test_seed_monthly_buckets_spans_a_year_boundary():
    monthly: dict[str, dict[str, float]] = {}
    seed_period_buckets(monthly, date(2025, 11, 1), date(2026, 2, 28), bucket_len=7)

    assert sorted(monthly) == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_seed_daily_buckets_fills_every_day_in_range():
    monthly: dict[str, dict[str, float]] = {}
    seed_period_buckets(monthly, date(2026, 9, 1), date(2026, 9, 5), bucket_len=10)

    assert sorted(monthly) == [
        "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05",
    ]


def test_seed_does_not_overwrite_a_bucket_already_populated_by_a_real_transaction():
    monthly = {"2026-03": {"month": "2026-03", "pendapatan": 500_000.0, "beban": 0.0}}
    seed_period_buckets(monthly, date(2026, 1, 1), date(2026, 3, 31), bucket_len=7)

    # Existing real data for March must survive the zero-fill pass (setdefault, not overwrite).
    assert monthly["2026-03"]["pendapatan"] == 500_000.0
    assert sorted(monthly) == ["2026-01", "2026-02", "2026-03"]
