"""Transaksi otomatis UU05 dari isian Manajemen Toko (pola sama dengan Imbal Hasil).

Setiap isian sel Minggu (toko_entries) punya satu transaksi + jurnal di unit UU05
dengan reference ``TOKO:<entry_id>``. Transaksi ini hanya bisa diubah/dihapus dari
menu Manajemen Toko; dari halaman Transaksi hanya bukti (proof) yang dikelola.
"""
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from modules.siabumdes.adapters.api.scope import assert_can_mutate_period, assert_not_readonly
from modules.siabumdes.application.transaction_journal import sync_transaction_journal
from modules.siabumdes.application.transaction_proofs import delete_transaction_proofs
from modules.siabumdes.infrastructure.models import Account, JournalEntry, Transaction, TransactionType, UnitUsaha

logger = logging.getLogger(__name__)

UNIT_CODE = "UU05"
REFERENCE_PREFIX = "TOKO:"
CASH = "1.1.01.15"
TYPE_BY_JENIS = {
    "pemasukan": ("pendapatan_usaha_unit5", "Pendapatan Usaha Unit 5", CASH, "4.1.01.15", "Pendapatan"),
    "pengeluaran": ("beban_operasional_unit5", "Beban Operasional Unit 5", "6.2.01.51", CASH, "Pengeluaran"),
}


async def ensure_toko_types(session):
    """Seed kedua jenis transaksi bila akun COA UU05-nya ada; konfigurasi live tidak ditimpa."""
    for code, name, debit, credit, _ in TYPE_BY_JENIS.values():
        found = (await session.execute(select(Account.code).where(
            Account.group_code == UNIT_CODE, Account.code.in_([debit, credit])))).scalars().all()
        if len(set(found)) == 2:
            await session.execute(insert(TransactionType).values(
                code=code, name=name, debit=debit, credit=credit, group_code=UNIT_CODE, unit_codes=[UNIT_CODE],
            ).on_conflict_do_nothing(constraint="uq_tx_types_code_group"))


def describe(jenis: str, item_name: str, week: int, period: str) -> str:
    year, month = period.split("-")
    return f"{TYPE_BY_JENIS[jenis][4]} {item_name} - Minggu {week} - {month}/{year}"


# Item Perduitan #1 berkata kunci kasbon dijurnal ke Piutang Lainnya, bukan Pendapatan/Beban.
KASBON = {
    "pengeluaran": ("kasbon_unit5", "Kasbon"),  # D Piutang Lainnya / K Kas
    "pemasukan": ("pelunasan_kasbon_unit5", "Pelunasan Kasbon"),  # D Kas / K Piutang Lainnya
}


def is_kasbon_item(name: str) -> bool:
    from modules.siabumdes.toko.application.neraca import is_piutang_lain

    return is_piutang_lain(name)


async def piutang_lain_account(session) -> str | None:
    """Kode akun Piutang Lainnya/Kasbon aktif di COA UU05 (dikenali dari namanya), atau None."""
    from modules.siabumdes.toko.application.journal import classify

    rows = (await session.scalars(select(Account).where(Account.group_code == UNIT_CODE, Account.active.is_(True))
                                  .order_by(Account.code))).all()
    for a in rows:
        if a.category and a.category.lower() == "aset" and classify(a.name, a.category, a.subcategory) == "piutang_lainnya":
            return a.code
    return None


async def toko_unit(session) -> UnitUsaha:
    unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.code == UNIT_CODE))
    if not unit:
        raise HTTPException(404, "Unit usaha UU05 belum tersedia")
    return unit


async def _kind(session, jenis: str) -> TransactionType:
    code = TYPE_BY_JENIS[jenis][0]
    kind = await session.scalar(select(TransactionType).where(
        TransactionType.code == code, TransactionType.group_code == UNIT_CODE))
    if not kind or not kind.debit or not kind.credit:
        await ensure_toko_types(session)
        kind = await session.scalar(select(TransactionType).where(
            TransactionType.code == code, TransactionType.group_code == UNIT_CODE))
    if not kind or not kind.debit or not kind.credit:
        raise HTTPException(422, f"Konfigurasikan jenis {code} beserta akun debit/kredit di COA UU05")
    return kind


async def _linked_tx(session, entry):
    if not entry.transaction_id:
        return None
    tx = await session.get(Transaction, entry.transaction_id, with_for_update=True)
    if not tx:
        raise HTTPException(409, "Transaksi terkait tidak ditemukan; hubungi admin")
    return tx


async def apply_entry_transaction(session, actor, unit, entry, item, *, check_period=True):
    """Buat/perbarui transaksi + jurnal UU05 untuk satu isian (tanggal, nominal, jenis, keterangan)."""
    if check_period:
        assert_not_readonly(actor)
    if entry.nominal is None or entry.nominal <= 0:
        raise HTTPException(422, "Nominal transaksi harus lebih dari nol untuk membuat transaksi")
    tx = await _linked_tx(session, entry)
    if check_period:
        if tx:
            await assert_can_mutate_period(session, actor, tx.date, unit.id)
        await assert_can_mutate_period(session, actor, entry.tanggal, unit.id)
    kasbon = await piutang_lain_account(session) if is_kasbon_item(item.name) else None
    tx = tx or Transaction(unit_usaha_id=unit.id, created_by=getattr(actor, "id", None) or "system", proofs=[])
    tx.date, tx.amount = entry.tanggal, entry.nominal
    if kasbon:
        code, label = KASBON[item.jenis]
        tx.transaction_type = code
        year, month = entry.period.split("-")
        tx.description = f"{label} {item.name} - Minggu {entry.week} - {month}/{year}"
        tx.debit_account_code, tx.credit_account_code = (kasbon, CASH) if item.jenis == "pengeluaran" else (CASH, kasbon)
    else:
        kind = await _kind(session, item.jenis)
        tx.transaction_type = kind.code
        tx.description = describe(item.jenis, item.name, entry.week, entry.period)
        tx.debit_account_code, tx.credit_account_code = kind.debit, kind.credit
    tx.reference = f"{REFERENCE_PREFIX}{entry.id}"  # entry harus sudah di-flush (id terisi)
    session.add(tx)
    await session.flush()
    await sync_transaction_journal(session, tx, unit.code)
    entry.transaction_id = tx.id
    session.add(entry)
    return tx


async def delete_entry_transaction(session, actor, unit, entry):
    """Hapus transaksi, jurnal, dan bukti yang terhubung ke isian (dipanggil sebelum isian dihapus)."""
    assert_not_readonly(actor)
    tx = await _linked_tx(session, entry)
    await assert_can_mutate_period(session, actor, tx.date if tx else entry.tanggal, unit.id)
    if not tx:
        return
    await delete_transaction_proofs(list(tx.proofs or []))
    entry.transaction_id = None
    await session.flush()  # lepas FK sebelum transaksi dihapus
    journal = await session.scalar(select(JournalEntry).where(JournalEntry.transaction_id == tx.id))
    if journal:
        await session.delete(journal)
        await session.flush()
    await session.delete(tx)


async def backfill_toko_transactions(session) -> int:
    """Boot: buat transaksi untuk isian lama (sebelum fitur ini) yang belum terhubung."""
    from modules.siabumdes.toko.infrastructure.models import TokoEntry, TokoItem

    unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.code == UNIT_CODE))
    if not unit:
        return 0
    rows = (await session.execute(select(TokoEntry, TokoItem).join(TokoItem, TokoItem.id == TokoEntry.item_id)
                                  .where(TokoEntry.transaction_id.is_(None), TokoEntry.nominal > 0))).all()
    for entry, item in rows:
        await apply_entry_transaction(session, None, unit, entry, item, check_period=False)
    await session.flush()
    return len(rows)


async def repost_kasbon_entries(session) -> int:
    """Boot (idempoten): jurnal otomatis isian kasbon yang masih ke Pendapatan/Beban ditulis ulang
    ke Piutang Lainnya <-> Kas. Isian pengguna tidak diubah/dihapus; hanya baris jurnal otomatisnya."""
    from modules.siabumdes.toko.infrastructure.models import TokoEntry, TokoItem

    unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.code == UNIT_CODE))
    acc = await piutang_lain_account(session)
    if not unit or not acc:
        if unit:
            logger.warning("akun Piutang Lainnya UU05 tidak ditemukan; jurnal kasbon Perduitan #1 tidak dipindah")
        return 0
    n = 0
    rows = (await session.execute(select(TokoEntry, TokoItem).join(TokoItem, TokoItem.id == TokoEntry.item_id)
                                  .where(TokoEntry.transaction_id.is_not(None)))).all()
    for entry, item in rows:
        if not is_kasbon_item(item.name):
            continue
        tx = await session.get(Transaction, entry.transaction_id)
        want = (acc, CASH) if item.jenis == "pengeluaran" else (CASH, acc)
        if tx is None or (tx.debit_account_code, tx.credit_account_code) == want:
            continue
        await apply_entry_transaction(session, None, unit, entry, item, check_period=False)
        n += 1
    await session.flush()
    return n


def default_week_date(period: str, week: int) -> str:
    """Sama dengan UI: Sabtu minggu ke-N (minggu Senin–Sabtu mulai Senin pertama bulan)."""
    from modules.siabumdes.toko.application.weeks import week_count, week_saturday

    return week_saturday(period, min(week, week_count(period))).isoformat()


async def reweek_toko_entries(session) -> tuple[int, int]:
    """Boot (idempoten): pindahkan nomor minggu isian lama ke minggu Senin–Sabtu sesuai tanggalnya.

    Hanya memindah periode/minggu; tidak pernah menggabung atau menghapus isian/transaksi.
    Bila sel tujuan (item, periode, minggu) sudah terisi, isian dibiarkan di sel lamanya dan dicatat di log.
    Return (dipindah, dilewati)."""
    from modules.siabumdes.toko.application.weeks import trade_week
    from modules.siabumdes.toko.infrastructure.models import TokoEntry, TokoItem

    unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.code == UNIT_CODE))
    rows = (await session.execute(select(TokoEntry, TokoItem).join(TokoItem, TokoItem.id == TokoEntry.item_id)
                                  .order_by(TokoEntry.tanggal, TokoEntry.id))).all()
    cells = {(e.item_id, e.period, e.week): e for e, _ in rows}
    moved = skipped = 0
    for entry, item in rows:
        period, week = trade_week(entry.tanggal)
        if (entry.period, entry.week) == (period, week):
            continue
        if (entry.item_id, period, week) in cells:
            logger.warning("isian toko %s (%s %s/%s, %s) tidak dipindah ke %s minggu %s: sel sudah terisi",
                           entry.id, item.name, entry.period, entry.week, entry.tanggal, period, week)
            skipped += 1
            continue
        cells.pop((entry.item_id, entry.period, entry.week), None)
        entry.period, entry.week = period, week
        cells[(entry.item_id, period, week)] = entry
        await session.flush()
        if unit and entry.transaction_id:
            await apply_entry_transaction(session, None, unit, entry, item, check_period=False)
        moved += 1
    await session.flush()
    return moved, skipped
