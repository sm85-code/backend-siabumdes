"""Dashboard balances and allocations follow accounting reports and entity scope."""
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'postgresql://user:pass@127.0.0.1:5432/siabumdes_test')

import pytest  # noqa: E402
from modules.siabumdes.application import reporting  # noqa: E402
from modules.siabumdes.application.bagi_hasil import BagiHasilConfig  # noqa: E402
from modules.siabumdes.money_json import stringify_money_fields  # noqa: E402


class ReportFixture(reporting.ReportingService):
    async def _accounts(self, group=None):
        if group != 'BUMDES':
            return []
        return [
            SimpleNamespace(code='101', name='Kas', category='aset', subcategory='kas_bank', normal_balance='debit'),
            SimpleNamespace(code='301', name='Penyertaan Modal Desa', category='ekuitas', subcategory='modal_desa', normal_balance='kredit'),
            SimpleNamespace(code='302', name='Modal Masyarakat', category='ekuitas', subcategory='modal_masyarakat', normal_balance='kredit'),
        ]

    async def _txs(self, *, start=None, end=None, unit_usaha_id=None, **kwargs):
        if unit_usaha_id:
            return []
        rows = [
            SimpleNamespace(date=date(2025, 1, 1), debit_account_code='101', credit_account_code='301', amount=Decimal('250000000')),
            SimpleNamespace(date=date(2026, 5, 1), debit_account_code='301', credit_account_code='101', amount=Decimal('50000000')),
            SimpleNamespace(date=date(2026, 6, 1), debit_account_code='101', credit_account_code='302', amount=Decimal('10000000')),
        ]
        return [t for t in rows if (not start or t.date >= start) and (not end or t.date <= end)]

    async def _units(self):
        return []

    async def _group_for(self, unit_usaha_id):
        return 'UU01' if unit_usaha_id else 'BUMDES'

    async def laba_rugi(self, *args):
        return {'total_pendapatan': 1500000, 'total_beban': 500000, 'laba_bersih': 1000000}


@pytest.mark.asyncio
@pytest.mark.parametrize('pades,modal', [(30, 18), (40, 8)])
async def test_dashboard_modal_balance_and_configured_allocation(monkeypatch, pades, modal):
    async def config(session):
        return BagiHasilConfig(*map(Decimal, (35, 7, 5, 5, pades, modal, 30, 70)))

    monkeypatch.setattr(reporting, 'get_bagi_hasil_config', config)
    result = await ReportFixture(None).dashboard(date(2026, 1, 1), date(2026, 12, 31), 'month', None, True)
    assert result['modal_desa'] == 200000000  # opening capital minus withdrawal; no other equity
    assert result['total_ekuitas'] == 210000000
    shares = {r['key']: r for r in result['bagi_hasil_bumdes']}
    assert shares['pades']['amount'] == pades * 10000
    assert shares['modal_bumdes']['amount'] == modal * 10000
    assert [r['key'] for r in result['bagi_hasil_bumdes']] == ['pades', 'modal_bumdes', 'penasihat', 'pengawas', 'pengurus', 'dana_sosial']
    assert sum(r['amount'] for r in shares.values()) == result['laba_bersih']
    serialized = stringify_money_fields(result)
    assert serialized['modal_desa'] == '200000000.00'
    assert serialized['bagi_hasil_bumdes'][0]['amount'] == f'{pades * 10000}.00'
    assert serialized['bagi_hasil_bumdes'][0]['persen'] == pades


@pytest.mark.asyncio
async def test_unit_dashboard_does_not_expose_bumdes_shares(monkeypatch):
    async def config(session):
        return BagiHasilConfig(*map(Decimal, (35, 7, 5, 5, 30, 18, 25, 75)))

    monkeypatch.setattr(reporting, 'get_bagi_hasil_config', config)
    result = await ReportFixture(None).dashboard(date(2026, 1, 1), date(2026, 12, 31), 'month', 'unit1', False)
    assert result['bagi_hasil_bumdes'] == []
    assert result['modal_desa'] == 0
    assert [r['amount'] for r in result['bagi_hasil_unit']] == [250000, 750000]
    assert result['unit_summaries'] == []


@pytest.mark.asyncio
async def test_unit_tables_use_as_of_balances_and_configured_shares(monkeypatch):
    class UnitsFixture(ReportFixture):
        async def _units(self):
            return [SimpleNamespace(id='unit1', code='UU01', name='Toko')]

        async def neraca(self, as_of, unit_usaha_id=None):
            assert as_of == date(2026, 12, 31)
            return dict(total_aset=9000000, total_kewajiban=1000000,
                        total_ekuitas=8000000, modal_desa=7000000, kas_bank=0)

    async def config(session):
        return BagiHasilConfig(*map(Decimal, (35, 7, 5, 5, 30, 18, 25, 75)))

    monkeypatch.setattr(reporting, 'get_bagi_hasil_config', config)
    result = await UnitsFixture(None).dashboard(date(2026, 1, 1), date(2026, 12, 31), 'month', None, True)
    row = stringify_money_fields(result)['unit_summaries'][0]
    assert row['modal_bumdes'] == '7000000.00'
    assert row['total_aset'] == '9000000.00'
    assert row['share_pengelola'] == '250000.00'
    assert row['share_bumdes'] == '750000.00'
