"""Postgres smoke test against the current shared.database module.

The previous version imported a removed top-level `database` module
(`applied_migrations`, Mongo-style `db.integration_test`, …) from an earlier
pre-SQLAlchemy iteration. That import fails whenever DATABASE_URL is set
(CI injects one via repo/org vars), so the test was a permanent false failure.

This replacement exercises the live stack: shared.database engine + SELECT 1.
Connection / auth / network failures skip (CI may point at an unreachable host);
a missing driver or broken URL rewrite still fails loudly.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError, InterfaceError


pytestmark = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")),
    reason="DATABASE_URL tidak tersedia",
)


@pytest.mark.asyncio
async def test_shared_database_engine_select_one():
    # Import after skipif so collection stays clean when URL is unset.
    from shared.database import engine

    try:
        async with engine.connect() as conn:
            value = (await conn.execute(text("SELECT 1"))).scalar_one()
    except (OperationalError, InterfaceError, DBAPIError, OSError, TimeoutError) as exc:
        pytest.skip(f"PostgreSQL integration unavailable: {exc}")

    assert value == 1
    # Sanity: URL rewrite landed on asyncpg even if the secret used +psycopg / bare postgresql.
    assert engine.url.drivername == "postgresql+asyncpg"
