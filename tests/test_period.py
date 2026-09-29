"""modules.siabumdes.period: both BUMDES (Pusat) and unit usaha (UU01..UU06) close
monthly ("YYYY-MM"). Deliberately DB-independent (no import of
modules.siabumdes.identity/siabumdes models, which require DATABASE_URL) so it runs
in CI same as everything else in this file's suite."""
from __future__ import annotations

from datetime import date

import pytest

from modules.siabumdes.period import (
    assert_period_kind_matches_group,
    period_kind,
    period_range,
)


def test_period_kind_classifies_month():
    assert period_kind("2026-09") == "month"


def test_period_kind_rejects_non_monthly_and_garbage():
    with pytest.raises(ValueError):
        period_kind("2026")
    with pytest.raises(ValueError):
        period_kind("2026-Q1")
    with pytest.raises(ValueError):
        period_kind("not-a-period")
    with pytest.raises(ValueError):
        period_kind("")


def test_period_range_month_handles_leap_february():
    assert period_range("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))


def test_bumdes_must_close_monthly():
    assert assert_period_kind_matches_group("2026-09", "BUMDES") == "month"
    with pytest.raises(ValueError):
        assert_period_kind_matches_group("2026-Q1", "BUMDES")
    with pytest.raises(ValueError):
        assert_period_kind_matches_group("2026", "BUMDES")


def test_unit_usaha_must_close_monthly():
    assert assert_period_kind_matches_group("2026-09", "UU01") == "month"
    with pytest.raises(ValueError):
        assert_period_kind_matches_group("2026-Q1", "UU01")
    with pytest.raises(ValueError):
        assert_period_kind_matches_group("2026", "UU05")
