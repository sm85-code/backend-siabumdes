"""Transfer bagi hasil: pelunasan saldo utang bagi hasil setelah tutup buku.

Butuh Postgres sungguhan (kolom JSONB/ARRAY), sama seperti test tutup buku lain.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio

if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
    pytest.skip("Requires a real Postgres DATABASE_URL", allow_module_level=True)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from modules.siabumdes.application.bagi_hasil_transfer import (  # noqa: E402
    list_bagi_hasil_transfers,
    run_bagi_hasil_transfer,
    split_amount,
    transfer_transactions,
    undo_bagi_hasil_transfer,
)
from modules.siabumdes.application.closing import undo_monthly_close  # noqa: E402
from modules.siabumdes.identity.infrastructure.models import (  # noqa: E402
    ClosedPeriod,
    OrgProfile,
    SystemControl,
    User,
)
from modules.siabumdes.infrastructure.models import (  # noqa: E402
    Account,
    JournalEntry,
    JournalItem,
    Transaction,
    UnitUsaha,
)
from shared.database import Base, DATABASE_URL as ASYNC_DATABASE_URL  # noqa: E402

_TABLES = [
    Account.__table__, Transaction.__table__, JournalEntry.__table__, JournalItem.__table__,
    UnitUsaha.__table__, ClosedPeriod.__table__, SystemControl.__table__, User.__table__,
    OrgProfile.__table__,
]


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(ASYNC_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=_TABLES))
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL unavailable for integration fixture: {exc}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=_TABLES))
    await engine.dispose()


def _acc(code, name, cat, slug, group, normal="debit"):
    return Account(code=code, name=name, category=cat, subcategory=slug,
                   normal_balance=normal, group_code=group, active=True)


async def _seed(session):
    unit = UnitUsaha(code="UU01", name="Unit Satu", active=True)
    session.add(unit)
    session.add_all([
        _acc("1.1.01.01", "Kas Pusat", "aset", "kas_bank", "BUMDES"),
        _acc("1.1.01.02", "Kas BJB", "aset", "kas_bank", "BUMDES"),
        _acc("2.1.03.01", "Utang BH BUMDES", "kewajiban", "utang_bagi_hasil_bumdes", "BUMDES", "kredit"),
        _acc("4.1.01.02", "Pendapatan Bagi Hasil", "pendapatan", "pendapatan_operasional", "BUMDES", "kredit"),
        _acc("3.9.01.01", "Ikhtisar", "ekuitas", "ikhtisar_laba_rugi", "BUMDES", "kredit"),
        _acc("1.1.01.11", "Kas UU01", "aset", "kas_bank", "UU01"),
        _acc("2.1.03.11", "Utang BH Unit", "kewajiban", "utang_bagi_hasil_unit", "UU01", "kredit"),
        _acc("3.9.01.11", "Ikhtisar UU01", "ekuitas", "ikhtisar_laba_rugi", "UU01", "kredit"),
    ])
    await session.flush()
    return unit


async def _credit_utang(session, *, when, amount, code, ikhtisar, unit_id=None):
    session.add(Transaction(
        date=when, unit_usaha_id=unit_id, transaction_type="jurnal_penutup",
        description="alokasi", amount=Decimal(amount), debit_account_code=ikhtisar,
        credit_account_code=code, created_by="admin-1", is_closing=True,
    ))
    await session.flush()


def _close(session, period, group):
    session.add(ClosedPeriod(period=period, group_code=group, laba_bersih=0, entries=0, closed_by="admin-1"))


def test_split_amount_sums_exactly_and_drops_zero_parts():
    parts = split_amount(Decimal("100.00"), [("a", Decimal("35")), ("b", Decimal("7")),
                                             ("c", Decimal("5")), ("d", Decimal("5"))])
    assert sum(a for _, a in parts) == Decimal("100.00")
    assert [l for l, _ in parts] == ["a", "b", "c", "d"]
    assert split_amount(Decimal("10"), [("a", Decimal("70")), ("b", Decimal("0"))]) == [("a", Decimal("10.00"))]
    with pytest.raises(ValueError):
        split_amount(Decimal("10"), [("a", Decimal("0"))])


@pytest.mark.asyncio
async def test_bumdes_only_quarter_end_months(session):
    await _seed(session)
    with pytest.raises(ValueError, match="Maret, Juni, September, Desember"):
        await run_bagi_hasil_transfer(session, period="2026-04", group="BUMDES", actor_id="a")


@pytest.mark.asyncio
async def test_not_closed_raises_belum_tutup_buku(session):
    await _seed(session)
    with pytest.raises(ValueError, match=r"Periode 2026-03 \(BUMDES\) Belum Tutup Buku"):
        await run_bagi_hasil_transfer(session, period="2026-03", group="BUMDES", actor_id="a")
    _close(session, "2026-03", "BUMDES")
    await session.flush()
    # Maret tutup, tapi Februari & Januari belum.
    with pytest.raises(ValueError, match=r"Periode 2026-02 \(BUMDES\) Belum Tutup Buku"):
        await run_bagi_hasil_transfer(session, period="2026-03", group="BUMDES", actor_id="a")


@pytest.mark.asyncio
async def test_bumdes_quarter_transfer_splits_accumulated_saldo(session):
    await _seed(session)
    for p in ("2026-01", "2026-02", "2026-03"):
        _close(session, p, "BUMDES")
    for when, amt in ((date(2026, 1, 31), "20000"), (date(2026, 2, 28), "17000"), (date(2026, 3, 31), "15000")):
        await _credit_utang(session, when=when, amount=amt, code="2.1.03.01", ikhtisar="3.9.01.01")
    await session.flush()

    res = await run_bagi_hasil_transfer(session, period="2026-03", group="BUMDES", actor_id="a")
    assert res["date"] == "2026-04-01" and res["total"] == "52000.00"
    txs = await transfer_transactions(session, "2026-03", "BUMDES")
    got = {t.transaction_type: t.amount for t in txs}
    assert got == {
        "bagi_hasil_pengurus": Decimal("35000.00"), "bagi_hasil_penasihat": Decimal("7000.00"),
        "bagi_hasil_pengawas": Decimal("5000.00"), "bagi_hasil_dana_sosial": Decimal("5000.00"),
    }
    assert all(t.debit_account_code == "2.1.03.01" and t.credit_account_code == "1.1.01.01"
               and t.date == date(2026, 4, 1) and t.unit_usaha_id is None for t in txs)
    n_entries = (await session.execute(select(JournalEntry))).scalars().all()
    assert len(n_entries) == 4

    with pytest.raises(ValueError, match="sudah dilakukan"):
        await run_bagi_hasil_transfer(session, period="2026-03", group="BUMDES", actor_id="a")

    # Saldo sudah nol: kuartal berikutnya tanpa laba baru tidak bisa ditransfer.
    for p in ("2026-04", "2026-05", "2026-06"):
        _close(session, p, "BUMDES")
    await session.flush()
    with pytest.raises(ValueError, match="nol"):
        await run_bagi_hasil_transfer(session, period="2026-06", group="BUMDES", actor_id="a")

    lst = await list_bagi_hasil_transfers(session)
    assert len(lst) == 1 and lst[0]["period"] == "2026-03" and lst[0]["total"] == "52000.00" and lst[0]["entries"] == 4

    # Tutup buku tidak boleh dibatalkan selama transfernya ada; setelah dibatalkan boleh.
    with pytest.raises(ValueError, match="Batalkan transfer"):
        await undo_monthly_close(session, "2026-03", "BUMDES")
    deleted = await undo_bagi_hasil_transfer(session, txs)
    assert deleted == 4
    assert (await session.execute(select(JournalEntry))).scalars().all() == []
    await undo_monthly_close(session, "2026-03", "BUMDES")


@pytest.mark.asyncio
async def test_unit_monthly_transfer_pays_bumdes_and_records_pusat_side(session):
    unit = await _seed(session)
    _close(session, "2026-12", "UU01")
    await _credit_utang(session, when=date(2026, 12, 31), amount="1000001", code="2.1.03.11",
                        ikhtisar="3.9.01.11", unit_id=unit.id)

    res = await run_bagi_hasil_transfer(session, period="2026-12", group="UU01", actor_id="a")
    assert res["date"] == "2027-01-01"
    txs = await transfer_transactions(session, "2026-12", "UU01")
    unit_txs = [t for t in txs if t.unit_usaha_id == unit.id]
    pusat_txs = [t for t in txs if t.unit_usaha_id is None]
    amounts = {t.transaction_type: t.amount for t in unit_txs}
    assert amounts == {"bagi_hasil_bumdes": Decimal("700000.70"), "bagi_hasil_pengelola": Decimal("300000.30")}
    assert sum(amounts.values()) == Decimal("1000001.00")
    assert all(t.debit_account_code == "2.1.03.11" and t.credit_account_code == "1.1.01.11" for t in unit_txs)
    assert len(pusat_txs) == 1
    p = pusat_txs[0]
    assert (p.debit_account_code, p.credit_account_code, p.amount) == ("1.1.01.01", "4.1.01.02", Decimal("700000.70"))
    assert p.date == date(2027, 1, 1)

    lst = await list_bagi_hasil_transfers(session)
    assert lst[0]["total"] == "1000001.00" and lst[0]["entries"] == 3

    # Pembatalan menghapus transaksi unit dan sisi Pusat sekaligus.
    assert await undo_bagi_hasil_transfer(session, txs) == 3
    assert await transfer_transactions(session, "2026-12", "UU01") == []


@pytest.mark.asyncio
async def test_older_period_transfer_blocked_when_newer_exists(session):
    unit = await _seed(session)
    for p in ("2026-01", "2026-02"):
        _close(session, p, "UU01")
    await _credit_utang(session, when=date(2026, 1, 31), amount="100", code="2.1.03.11",
                        ikhtisar="3.9.01.11", unit_id=unit.id)
    await run_bagi_hasil_transfer(session, period="2026-02", group="UU01", actor_id="a")
    with pytest.raises(ValueError, match="lebih baru"):
        await run_bagi_hasil_transfer(session, period="2026-01", group="UU01", actor_id="a")
