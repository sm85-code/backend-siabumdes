"""Manajemen Toko: isian mingguan membuat/menyinkronkan transaksi + jurnal UU05."""
import os
from datetime import date
from decimal import Decimal

import pytest

if not os.getenv('RISKA_TEST_DATABASE_URL'):
    pytest.skip('Requires isolated PostgreSQL', allow_module_level=True)

import test_riska_dialogue_pg as fixture_source  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from modules.siabumdes.adapters.api.v1 import toko_router as tr  # noqa: E402
from modules.siabumdes.adapters.api.v1.transaction_router import _assert_not_bagi_hasil  # noqa: E402
from modules.siabumdes.application.toko_transactions import backfill_toko_transactions, ensure_toko_types  # noqa: E402
from modules.siabumdes.infrastructure.models import Account, JournalEntry, Transaction, TransactionType  # noqa: E402
from modules.siabumdes.toko.infrastructure.models import TokoEntry, TokoItem  # noqa: E402

toko_context = fixture_source.context


class _NoCommit:
    """Bungkus session supaya commit() di handler jadi flush (fixture memakai satu transaksi)."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    async def commit(self):
        await self._s.flush()


async def _setup(session, unit):
    session.add(Account(code='6.2.01.51', name='Beban Operasional Umum', category='beban', subcategory='beban_operasional',
                        normal_balance='debit', group_code='UU05', unit_usaha_id=unit.id))
    await session.flush()
    await ensure_toko_types(session)
    await session.flush()


@pytest.mark.asyncio
async def test_entry_lifecycle_syncs_unit5_transaction(toko_context):
    session, admin, manager, _, unit, *_ = toko_context
    await _setup(session, unit)
    s = _NoCommit(session)
    types = {t.code: t for t in (await session.execute(select(TransactionType).where(TransactionType.group_code == 'UU05'))).scalars()}
    assert types['pendapatan_usaha_unit5'].debit == '1.1.01.15' and types['pendapatan_usaha_unit5'].credit == '4.1.01.15'
    assert types['beban_operasional_unit5'].debit == '6.2.01.51' and types['beban_operasional_unit5'].credit == '1.1.01.15'

    item = await tr.create_item(tr.ItemCreateBody(name='Penjualan Sembako', jenis='pemasukan'), manager, s)
    out = await tr.upsert_entry(tr.EntryUpsertBody(item_id=item['id'], period='2026-10', week=2,
                                                   tanggal=date(2026, 10, 10), nominal=Decimal('1500000')), manager, s)
    tx = await session.get(Transaction, out['transaction_id'])
    assert tx.unit_usaha_id == unit.id and tx.transaction_type == 'pendapatan_usaha_unit5'
    assert tx.description == 'Pendapatan Penjualan Sembako - Minggu 2 - 10/2026'
    assert tx.amount == Decimal('1500000') and tx.date == date(2026, 10, 10)
    assert tx.reference == f"TOKO:{out['id']}"
    assert await session.scalar(select(func.count(JournalEntry.id)).where(JournalEntry.transaction_id == tx.id)) == 1
    with pytest.raises(HTTPException):
        _assert_not_bagi_hasil(tx.reference)

    # ubah sel -> transaksi yang sama diperbarui
    await tr.upsert_entry(tr.EntryUpsertBody(item_id=item['id'], period='2026-10', week=2,
                                             tanggal=date(2026, 10, 12), nominal=Decimal('2000000')), manager, s)
    await session.refresh(tx)
    assert tx.amount == Decimal('2000000') and tx.date == date(2026, 10, 12)

    # ganti nama + jenis -> keterangan/jenis/akun ikut
    await tr.update_item(item['id'], tr.ItemUpdateBody(name='Belanja Stok', jenis='pengeluaran'), manager, s)
    await session.refresh(tx)
    assert tx.transaction_type == 'beban_operasional_unit5'
    assert tx.description == 'Pengeluaran Belanja Stok - Minggu 2 - 10/2026'
    assert (tx.debit_account_code, tx.credit_account_code) == ('6.2.01.51', '1.1.01.15')

    # hapus sel -> transaksi + jurnal hilang
    await tr.delete_entry(out['id'], manager, s)
    await session.flush()
    assert await session.get(Transaction, tx.id) is None
    assert await session.scalar(select(func.count(JournalEntry.id)).where(JournalEntry.transaction_id == tx.id)) == 0


@pytest.mark.asyncio
async def test_backfill_links_legacy_entries(toko_context):
    session, admin, _, _, unit, *_ = toko_context
    await _setup(session, unit)
    item = TokoItem(name='Listrik', jenis='pengeluaran')
    session.add(item)
    await session.flush()
    entry = TokoEntry(item_id=item.id, period='2026-09', week=1, tanggal=date(2026, 9, 5), nominal=Decimal('99000'))
    session.add(entry)
    await session.flush()
    assert await backfill_toko_transactions(session) == 1
    tx = await session.get(Transaction, entry.transaction_id)
    assert tx.description == 'Pengeluaran Listrik - Minggu 1 - 09/2026'
    assert await backfill_toko_transactions(session) == 0
