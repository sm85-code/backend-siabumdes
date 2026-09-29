from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio

# Skip before importing any project module: they pull in shared.database,
# which raises at import time when DATABASE_URL is unset (as in CI).
if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
    pytest.skip("Requires a real Postgres DATABASE_URL", allow_module_level=True)

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from modules.siabumdes.adapters.api.scope import assert_can_mutate_period  # noqa: E402
from modules.siabumdes.identity.infrastructure.models import ClosedPeriod, SystemControl, User  # noqa: E402
from modules.siabumdes.application.closing import run_monthly_close, undo_monthly_close, SUB_UTANG_BH_UNIT  # noqa: E402
from modules.siabumdes.infrastructure.models import Account, Transaction, UnitUsaha  # noqa: E402
from shared.database import Base, DATABASE_URL as ASYNC_DATABASE_URL  # noqa: E402
from modules.siabumdes.coa_taxonomy import SUB_IKHTISAR_LR, SUB_SALDO_LABA  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL.startswith("postgresql"), reason="needs postgres")

_TABLES = None

def _tables():
    global _TABLES
    if _TABLES is None:
        from modules.siabumdes.infrastructure.models import JournalEntry, JournalItem, Transaction
        _TABLES = [Account.__table__, Transaction.__table__, JournalEntry.__table__, JournalItem.__table__,
                   UnitUsaha.__table__, ClosedPeriod.__table__, SystemControl.__table__, User.__table__]
    return _TABLES

@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(ASYNC_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=_tables()))
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_tables()))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL unavailable for integration fixture: {exc}")
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_local() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=_tables()))
    await engine.dispose()

async def _seed_unit(session):
    unit = UnitUsaha(code="UU01", name="Unit Dagang", active=True)
    session.add(unit)
    slugs = [SUB_IKHTISAR_LR, SUB_SALDO_LABA, SUB_UTANG_BH_UNIT]
    for i, slug in enumerate(slugs):
        session.add(Account(code=f"8{i}00", name=slug, category="ekuitas", subcategory=slug,
                             normal_balance="kredit", group_code="UU01", active=True))
    await session.flush()
    return unit

@pytest.mark.asyncio
async def test_closed_unit_usaha_period_blocks_new_transactions(session):
    unit = await _seed_unit(session)
    result = await run_monthly_close(session, period="2026-09", group="UU01", actor_id="admin-1")
    assert result["closed"] is True and result["group"] == "UU01"

    user = User(username="bendahara2", email="b2@test.local", name="B", password_hash="x",
                role="bendahara", active=True)
    session.add(user)
    await session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await assert_can_mutate_period(session, user, "2026-09-15", unit_usaha_id=unit.id)
    assert exc_info.value.status_code == 400
    assert "UU01" in exc_info.value.detail

    # BUMDES-level (unit_usaha_id=None) same month must remain unaffected.
    await assert_can_mutate_period(session, user, "2026-09-20", unit_usaha_id=None)
    # Different unit, same month, must remain unaffected (per-group isolation).
    await assert_can_mutate_period(session, user, "2026-09-20", unit_usaha_id="nonexistent-unit-id")

@pytest.mark.asyncio
async def test_double_close_unit_usaha_period_rejected(session):
    await _seed_unit(session)
    await run_monthly_close(session, period="2026-09", group="UU01", actor_id="admin-1")
    with pytest.raises(ValueError):
        await run_monthly_close(session, period="2026-09", group="UU01", actor_id="admin-1")

    rows = (await session.execute(select(ClosedPeriod).where(ClosedPeriod.group_code == "UU01"))).scalars().all()
    assert len(rows) == 1

@pytest.mark.asyncio
async def test_unit_usaha_reopen_then_can_close_again(session):
    await _seed_unit(session)
    await run_monthly_close(session, period="2026-09", group="UU01", actor_id="admin-1")
    await undo_monthly_close(session, "2026-09", "UU01")
    rows = (await session.execute(select(ClosedPeriod).where(ClosedPeriod.group_code == "UU01"))).scalars().all()
    assert len(rows) == 0
    result = await run_monthly_close(session, period="2026-09", group="UU01", actor_id="admin-1")
    assert result["closed"] is True


@pytest.mark.asyncio
async def test_closing_zeroes_a_pendapatan_account_whose_period_balance_is_reversed(session):
    """A pendapatan account normally carries a credit balance for the
    period. If a correcting entry debits it for more than it was
    credited (e.g. fixing an earlier overstated posting), _net_rows
    reports a *negative* amount for that account -- laba_bersih already
    accounts for it correctly, but the account itself must still be
    closed to zero via the opposite pair (credit acc / debit ikhtisar),
    not skipped."""
    unit = await _seed_unit(session)
    session.add(
        Account(code="4100", name="Pendapatan Usaha", category="pendapatan",
                 subcategory="pendapatan_operasional", normal_balance="kredit",
                 group_code="UU01", active=True)
    )
    session.add(
        Account(code="1100", name="Kas", category="aset", subcategory="kas_bank",
                 normal_balance="debit", group_code="UU01", active=True)
    )
    await session.flush()

    # Correcting entry: debit the revenue account directly (credit kas),
    # netting it to a reversed (debit) balance of -500_000 for the period.
    session.add(
        Transaction(
            date=date(2026, 9, 10), unit_usaha_id=unit.id, transaction_type="koreksi",
            description="Koreksi pendapatan lebih catat", amount=Decimal("500000"),
            debit_account_code="4100", credit_account_code="1100",
            created_by="admin-1",
        )
    )
    await session.flush()

    result = await run_monthly_close(session, period="2026-09", group="UU01", actor_id="admin-1")
    assert result["outcome"] == "rugi"  # only a reversed-revenue debit this month -> a loss

    closing_txs = (
        await session.execute(
            select(Transaction).where(Transaction.is_closing.is_(True), Transaction.reference.contains("UU01"))
        )
    ).scalars().all()
    pend_close = [t for t in closing_txs if t.credit_account_code == "4100" or t.debit_account_code == "4100"]
    assert len(pend_close) == 1, "the reversed pendapatan account must still get exactly one closing entry"
    # Reversed balance closes via the opposite pair: credit the account, debit ikhtisar.
    assert pend_close[0].credit_account_code == "4100"
    assert pend_close[0].amount == Decimal("500000.00")

    # The account's net balance across all its transactions (original +
    # closing) must be exactly zero once the period is closed.
    all_txs = (await session.execute(select(Transaction))).scalars().all()
    net = Decimal("0")
    for tx in all_txs:
        if tx.debit_account_code == "4100":
            net -= tx.amount
        if tx.credit_account_code == "4100":
            net += tx.amount
    assert net == Decimal("0")
