"""Regression test: an internal cash<->bank transfer must not inflate
ReportingService.arus_kas().

Before this fix, a transaction whose debit AND credit account were both
subcategory "kas_bank" (e.g. setor tunai dari Kas ke Bank BJB, atau mutasi
antar rekening/kas pusat) was counted as pure "kas masuk" only -- the
if/elif in arus_kas() never evaluated the elif branch once the if matched,
so the matching "kas keluar" leg was silently dropped. Net effect: money
that never left the BUMDes (it just moved between its own cash/bank
accounts) inflated arus_kas_bersih.

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from modules.siabumdes.application.reporting import ReportingService  # noqa: E402
from modules.siabumdes.infrastructure.models import Account, Transaction, UnitUsaha  # noqa: E402
from shared.database import Base, DATABASE_URL as ASYNC_DATABASE_URL  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")
_TABLES = [UnitUsaha.__table__, Account.__table__, Transaction.__table__]


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


async def _account(session, code, name, category, subcategory, normal_balance):
    acc = Account(
        code=code, name=name, category=category, subcategory=subcategory,
        normal_balance=normal_balance, group_code="BUMDES",
    )
    session.add(acc)
    await session.flush()
    return acc


async def _tx(session, *, debit, credit, amount, tx_date):
    session.add(Transaction(
        date=tx_date, transaction_type="mutasi", description="mutasi kas",
        amount=Decimal(str(amount)), debit_account_code=debit, credit_account_code=credit,
        created_by="tester",
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_internal_cash_transfer_is_excluded_from_cash_flow(session: AsyncSession):
    await _account(session, "kas", "Kas Pusat", "aset", "kas_bank", "debit")
    await _account(session, "bank_bjb", "Bank BJB", "aset", "kas_bank", "debit")
    await _account(session, "pendapatan_jasa", "Pendapatan Jasa", "pendapatan", "", "kredit")

    d = date(2026, 1, 15)
    # Mutasi internal: Bank BJB -> Kas Pusat (kedua akun sama-sama kas_bank).
    await _tx(session, debit="kas", credit="bank_bjb", amount=1_000_000, tx_date=d)
    # Pendapatan riil masuk ke Bank BJB -- ini harus tetap terhitung sebagai kas masuk.
    await _tx(session, debit="bank_bjb", credit="pendapatan_jasa", amount=500_000, tx_date=d)
    await session.commit()

    result = await ReportingService(session).arus_kas(date(2026, 1, 1), date(2026, 1, 31))

    assert result["total_masuk"] == 500_000
    assert result["total_keluar"] == 0
    assert result["arus_kas_bersih"] == 500_000
    assert len(result["kas_masuk"]) == 1
    assert len(result["kas_keluar"]) == 0
