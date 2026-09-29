"""Regression test for the BUMDES period-close enforcement bug: a closed
BUMDES-level (unit_usaha_id is None) period must actually block new
transactions dated inside it, via modules.siabumdes.adapters.api.scope.assert_can_mutate_period
-- the guard that transaction_router.py / io_router.py call.

Before this fix, BUMDES closes were stored as quarter/year strings (e.g.
"2026-Q1") while the guard only ever compared against a naive "YYYY-MM"
string, so it could never match and silently never blocked anything. The
fix reverts BUMDES closing to monthly ("YYYY-MM"), same as unit usaha, so
the existing naive string-match guard works correctly again.

Runs against a real (Postgres) async session, since Account/Transaction use
Postgres-only column types (JSONB/ARRAY) that don't work against SQLite.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

# Skip before importing any project module: they pull in shared.database,
# which raises at import time when DATABASE_URL is unset (as in CI).
if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
    pytest.skip("Requires a real Postgres DATABASE_URL", allow_module_level=True)

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from modules.siabumdes.adapters.api.scope import assert_can_mutate_period  # noqa: E402
from modules.siabumdes.identity.infrastructure.models import ClosedPeriod, SystemControl, User  # noqa: E402
from modules.siabumdes.application.closing import run_monthly_close  # noqa: E402
from modules.siabumdes.infrastructure.models import Account  # noqa: E402
from shared.database import Base, DATABASE_URL as ASYNC_DATABASE_URL  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="Requires a real Postgres DATABASE_URL (Account/Transaction use JSONB/ARRAY columns)",
)

_TABLES = None


def _tables():
    global _TABLES
    if _TABLES is None:
        from modules.siabumdes.infrastructure.models import (
            JournalEntry,
            JournalItem,
            Transaction,
            UnitUsaha,
        )

        _TABLES = [
            Account.__table__,
            Transaction.__table__,
            JournalEntry.__table__,
            JournalItem.__table__,
            UnitUsaha.__table__,
            ClosedPeriod.__table__,
            SystemControl.__table__,
            User.__table__,
        ]
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


async def _seed_bumdes_accounts(session: AsyncSession) -> None:
    from modules.siabumdes.coa_taxonomy import (
        SUB_BAGI_HASIL_DESA,
        SUB_IKHTISAR_LR,
        SUB_LABA_DICADANGKAN,
        SUB_SALDO_LABA,
    )
    from modules.siabumdes.application.closing import SUB_UTANG_BH_BUMDES

    slugs = [SUB_IKHTISAR_LR, SUB_BAGI_HASIL_DESA, SUB_LABA_DICADANGKAN, SUB_SALDO_LABA, SUB_UTANG_BH_BUMDES]
    for i, slug in enumerate(slugs):
        session.add(
            Account(
                code=f"9{i}00",
                name=slug,
                category="ekuitas",
                subcategory=slug,
                normal_balance="kredit",
                group_code="BUMDES",
                active=True,
            )
        )
    await session.flush()


@pytest.mark.asyncio
async def test_closing_a_bumdes_period_uses_monthly_format(session):
    await _seed_bumdes_accounts(session)
    result = await run_monthly_close(session, period="2026-09", group="BUMDES", actor_id="admin-1")
    assert result["closed"] is True
    assert result["period"] == "2026-09"

    from sqlalchemy import select

    closed = (
        await session.execute(
            select(ClosedPeriod).where(ClosedPeriod.group_code == "BUMDES")
        )
    ).scalars().all()
    assert len(closed) == 1
    # This is the crux of the bug: BUMDES closes must be stored as "YYYY-MM",
    # not "2026-Q3"/"2026", so the naive string-match guard in scope.py can
    # actually match them.
    assert closed[0].period == "2026-09"


@pytest.mark.asyncio
async def test_bumdes_quarter_or_year_periods_are_rejected(session):
    await _seed_bumdes_accounts(session)
    with pytest.raises(ValueError):
        await run_monthly_close(session, period="2026-Q1", group="BUMDES", actor_id="admin-1")
    with pytest.raises(ValueError):
        await run_monthly_close(session, period="2026", group="BUMDES", actor_id="admin-1")


@pytest.mark.asyncio
async def test_closed_bumdes_period_blocks_new_transactions_via_the_real_guard(session):
    """This is the actual regression check: close a BUMDES-level (unit_usaha_id
    is None) period, then verify assert_can_mutate_period -- the guard
    transaction_router.py / io_router.py call before posting -- rejects a new
    transaction dated inside that closed month."""
    await _seed_bumdes_accounts(session)
    await run_monthly_close(session, period="2026-09", group="BUMDES", actor_id="admin-1")

    user = User(
        username="bendahara",
        email="bendahara@test.local",
        name="Bendahara",
        password_hash="x",
        role="bendahara",
        active=True,
    )
    session.add(user)
    await session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await assert_can_mutate_period(session, user, "2026-09-15", unit_usaha_id=None)
    assert exc_info.value.status_code == 400
    assert "2026-09" in exc_info.value.detail
    assert "BUMDES" in exc_info.value.detail

    # A different, still-open month must still be allowed.
    await assert_can_mutate_period(session, user, "2026-10-01", unit_usaha_id=None)
