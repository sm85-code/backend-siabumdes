import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/test")

from modules.siabumdes.application.riska_ai import budget_summary


def test_budget_stops_before_next_reservation_exceeds_cap():
    now = datetime(2026, 10, 12, 23, 59, tzinfo=ZoneInfo('Asia/Jakarta'))
    result = budget_summary(8, Decimal('0.48'), Decimal('0.50'), 20, now, {'failed': 2})
    assert result['exhausted'] and result['remaining_calls'] == 0
    assert 'cadangan' in result['reason']
    assert result['resets_at'] == '2026-10-13T00:00:00+07:00'
    assert result['statuses']['failed'] == 2


def test_call_limit_and_currency_limit_both_apply():
    now = datetime.now(ZoneInfo('Asia/Jakarta'))
    assert budget_summary(0, Decimal('0'), Decimal('0.50'), 20, now)['remaining_calls'] == 8
    result = budget_summary(2, Decimal('0.12'), Decimal('5'), 2, now)
    assert result['exhausted'] and 'jumlah' in result['reason']


def test_twenty_calls_fit_new_daily_default():
    now = datetime.now(ZoneInfo('Asia/Jakarta'))
    result = budget_summary(8, Decimal('0.48'), Decimal('1.20'), 20, now)
    assert result['remaining_calls'] == 12 and not result['exhausted']
    assert budget_summary(20, Decimal('1.20'), Decimal('1.20'), 20, now)['exhausted']


def test_agent_periods_have_one_source_and_handle_year_boundary():
    from datetime import date
    import pytest
    from pydantic import ValidationError
    from modules.siabumdes.application.riska_agent_tools import Report
    assert Report(report='profit_loss', last_months=6).dates(date(2026, 2, 10)) == (date(2025, 9, 1), date(2026, 2, 10))
    assert Report(report='profit_loss', year=2026).dates(date(2026, 10, 12)) == (date(2026, 1, 1), date(2026, 10, 12))
    with pytest.raises(ValidationError):
        Report(report='profit_loss', year=2026, last_months=6)
