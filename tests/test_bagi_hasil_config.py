"""Regression test: proporsi bagi hasil dibaca dari OrgProfile (bisa diedit
lewat menu Profil BUMDES), bukan lagi konstanta hardcode BUMDES_ALLOC yang
dulu ada di closing.py.

Membuktikan run_monthly_close() memposting jurnal penutup sesuai ANGKA
KUSTOM di OrgProfile (bukan default 52/30/18) -- kalau closing.py masih
mengacu ke konstanta lama, test ini akan gagal karena jumlah yang diposting
tidak cocok dengan proporsi kustom yang di-set di sini.

Runs against a real (Postgres) async session, since Account/Transaction use
Postgres-only column types (JSONB/ARRAY) that don't work against SQLite.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest

if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
    pytest.skip("Requires a real Postgres DATABASE_URL", allow_module_level=True)

import pytest_asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from modules.siabumdes.application.bagi_hasil import validate_bagi_hasil_fields  # noqa: E402
from modules.siabumdes.application.closing import SUB_UTANG_BH_BUMDES, run_monthly_close  # noqa: E402
from modules.siabumdes.coa_taxonomy import SUB_BAGI_HASIL_DESA, SUB_IKHTISAR_LR, SUB_LABA_DICADANGKAN, SUB_SALDO_LABA  # noqa: E402
from modules.siabumdes.identity.infrastructure.models import ClosedPeriod, OrgProfile  # noqa: E402
from modules.siabumdes.infrastructure.models import (  # noqa: E402
    Account,
    JournalEntry,
    JournalItem,
    Transaction,
    UnitUsaha,
)
from shared.database import Base, DATABASE_URL as ASYNC_DATABASE_URL  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")
_TABLES = [
    OrgProfile.__table__,
    UnitUsaha.__table__,
    Account.__table__,
    Transaction.__table__,
    JournalEntry.__table__,
    JournalItem.__table__,
    ClosedPeriod.__table__,
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
        # Honest skip: driver/URL wiring is covered elsewhere; an unreachable CI
        # DATABASE_URL must not fail the job as if application code broke.
        pytest.skip(f"PostgreSQL unavailable for integration fixture: {exc}")
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_local() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=_TABLES))
    await engine.dispose()


async def _seed_bumdes_accounts(session: AsyncSession) -> None:
    slugs = [
        (SUB_IKHTISAR_LR, "ekuitas"),
        (SUB_BAGI_HASIL_DESA, "ekuitas"),
        (SUB_LABA_DICADANGKAN, "ekuitas"),
        (SUB_SALDO_LABA, "ekuitas"),
        (SUB_UTANG_BH_BUMDES, "kewajiban"),
    ]
    for i, (slug, category) in enumerate(slugs):
        session.add(Account(
            code=f"9{i}00", name=slug, category=category, subcategory=slug,
            normal_balance="kredit", group_code="BUMDES", active=True,
        ))
    session.add(Account(
        code="1100", name="Kas", category="aset", subcategory="kas_bank",
        normal_balance="debit", group_code="BUMDES", active=True,
    ))
    session.add(Account(
        code="4100", name="Pendapatan Jasa", category="pendapatan", subcategory="pendapatan_operasional",
        normal_balance="kredit", group_code="BUMDES", active=True,
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_closing_posts_amounts_from_custom_org_profile_config(session: AsyncSession):
    await _seed_bumdes_accounts(session)
    # Laba bersih periode ini: 10.000.000 (satu transaksi pendapatan, tanpa beban).
    session.add(Transaction(
        date=date(2026, 9, 15), transaction_type="jasa", description="Pendapatan jasa",
        amount=Decimal("10000000"), debit_account_code="1100", credit_account_code="4100",
        created_by="tester",
    ))
    # Proporsi KUSTOM -- sengaja dipilih supaya jumlah tiap bucket (utang
    # bagi hasil / pades / modal) beda dari default (52/30/18), bukan cuma
    # rincian di dalamnya, supaya test ini benar-benar gagal kalau
    # closing.py diam-diam kembali pakai BUMDES_ALLOC hardcode lama alih-alih
    # baca dari OrgProfile.
    session.add(OrgProfile(
        id="default",
        share_pengurus=Decimal("50"), share_penasihat=Decimal("10"),
        share_pengawas=Decimal("0"), share_dana_sosial=Decimal("0"),
        share_pades=Decimal("25"), share_modal_bumdes=Decimal("15"),
    ))
    await session.commit()

    result = await run_monthly_close(session, period="2026-09", group="BUMDES", actor_id="admin-1")
    assert result["closed"] is True

    txs = (
        await session.execute(
            select(Transaction).where(Transaction.reference == "CLOSE-2026-09-BUMDES")
        )
    ).scalars().all()
    by_credit = {tx.credit_account_code: tx.amount for tx in txs if tx.debit_account_code == "9000"}

    # 50+10+0+0 = 60% dari 10.000.000 = 6.000.000 -> akun utang_bagi_hasil_bumdes (kode 9400).
    assert by_credit["9400"] == Decimal("6000000.00")
    # PADes 25% -> akun bagi_hasil_desa (kode 9100).
    assert by_credit["9100"] == Decimal("2500000.00")
    # Penguatan modal 15% -> akun laba_dicadangkan (kode 9200).
    assert by_credit["9200"] == Decimal("1500000.00")


def test_validate_bagi_hasil_fields_rejects_non_100_total():
    with pytest.raises(Exception):
        validate_bagi_hasil_fields({
            "share_pengurus": 40, "share_penasihat": 10, "share_pengawas": 2,
            "share_dana_sosial": 0, "share_pades": 30, "share_modal_bumdes": 10,  # total 92
        })


def test_validate_bagi_hasil_fields_accepts_100_total():
    validate_bagi_hasil_fields({
        "share_pengurus": 40, "share_penasihat": 10, "share_pengawas": 2,
        "share_dana_sosial": 0, "share_pades": 30, "share_modal_bumdes": 18,  # total 100
    })
