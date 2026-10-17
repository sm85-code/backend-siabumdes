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
from modules.siabumdes.application.toko_transactions import backfill_toko_transactions, ensure_toko_types, reweek_toko_entries  # noqa: E402
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
                                                   tanggal=date(2026, 10, 17), nominal=Decimal('1500000')), manager, s)
    tx = await session.get(Transaction, out['transaction_id'])
    assert tx.unit_usaha_id == unit.id and tx.transaction_type == 'pendapatan_usaha_unit5'
    assert tx.description == 'Pendapatan Penjualan Sembako - Minggu 2 - 10/2026'
    assert tx.amount == Decimal('1500000') and tx.date == date(2026, 10, 17)
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


@pytest.mark.asyncio
async def test_reweek_only_moves_never_merges_or_deletes(toko_context):
    session, admin, _, _, unit, *_ = toko_context
    await _setup(session, unit)
    item = TokoItem(name='Saldo', jenis='pemasukan')
    session.add(item)
    await session.flush()
    # Minggu lama (tgl 1 s.d. Sabtu pertama): Sep w5 = 28–30 Sep, Okt w1 = 1–3 Okt, Okt w2 = 4–10 Okt.
    a = TokoEntry(item_id=item.id, period='2026-09', week=5, tanggal=date(2026, 9, 29), nominal=Decimal('100'))
    b = TokoEntry(item_id=item.id, period='2026-10', week=1, tanggal=date(2026, 10, 2), nominal=Decimal('50'))
    c = TokoEntry(item_id=item.id, period='2026-10', week=2, tanggal=date(2026, 10, 8), nominal=Decimal('70'))
    session.add_all([a, b, c])
    await session.flush()
    assert await backfill_toko_transactions(session) == 3
    txs = {e.id: e.transaction_id for e in (a, b, c)}
    assert await reweek_toko_entries(session) == (1, 2)
    rows = {(e.period, e.week): e for e in (await session.execute(select(TokoEntry))).scalars()}
    # a pindah ke Sep w4; b (2 Okt) mestinya juga Sep w4 tapi sel terisi -> tetap di Okt w1;
    # c (8 Okt) mestinya Okt w1 yang masih ditempati b -> tetap di Okt w2. Tidak ada yang digabung/dihapus.
    assert {k: e.id for k, e in rows.items()} == {('2026-09', 4): a.id, ('2026-10', 1): b.id, ('2026-10', 2): c.id}
    assert [rows[k].nominal for k in sorted(rows)] == [Decimal('100'), Decimal('50'), Decimal('70')]
    for e in (a, b, c):
        assert (await session.get(Transaction, txs[e.id])).amount == e.nominal
    assert (await session.get(Transaction, a.transaction_id)).description == 'Pendapatan Saldo - Minggu 4 - 09/2026'
    assert await reweek_toko_entries(session) == (0, 2)


@pytest.mark.asyncio
async def test_kasbon_entries_post_to_piutang_lainnya(toko_context):
    from modules.siabumdes.application.toko_transactions import repost_kasbon_entries

    session, admin, _, _, unit, *_ = toko_context
    await _setup(session, unit)
    item = TokoItem(name='Kasbon Karyawan', jenis='pengeluaran')
    session.add(item)
    await session.flush()
    entry = TokoEntry(item_id=item.id, period='2026-10', week=2, tanggal=date(2026, 10, 14), nominal=Decimal('40000'))
    session.add(entry)
    await session.flush()
    # Tanpa akun Piutang Lainnya: tetap jurnal lama (Beban), tidak ada yang ditulis ulang.
    assert await backfill_toko_transactions(session) == 1
    tx = await session.get(Transaction, entry.transaction_id)
    assert tx.debit_account_code == '6.2.01.51'
    assert await repost_kasbon_entries(session) == 0
    session.add(Account(code='1.1.04.15', name='Piutang Lainnya (Kasbon)', category='aset', subcategory='aset_lancar',
                        normal_balance='debit', group_code='UU05', unit_usaha_id=unit.id))
    await session.flush()
    assert await repost_kasbon_entries(session) == 1
    await session.refresh(tx)
    assert (tx.debit_account_code, tx.credit_account_code) == ('1.1.04.15', '1.1.01.15')
    assert tx.id == entry.transaction_id and tx.amount == Decimal('40000')
    assert tx.description == 'Kasbon Kasbon Karyawan - Minggu 2 - 10/2026'
    assert await session.scalar(select(func.count(JournalEntry.id)).where(JournalEntry.transaction_id == tx.id)) == 1
    assert await session.get(TokoEntry, entry.id) is not None
    assert await repost_kasbon_entries(session) == 0
