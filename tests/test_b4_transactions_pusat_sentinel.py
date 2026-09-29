"""SIABUMDES B4 — GET /transactions pusat sentinel (unit_usaha_id empty / __null__).

Matches export behavior: omit = all units; "" or "__null__" = IS NULL (pusat).
Unit tests (no Postgres). DATABASE_URL placeholder for model import.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1:5432/siabumdes_test")

import pytest
from fastapi import Response

from modules.siabumdes.adapters.api.v1.transaction_router import list_transactions


def _tx_row(**overrides):
    base = dict(
        id="tx-1",
        date=date(2026, 9, 1),
        unit_usaha_id=None,
        transaction_type="pendapatan",
        description="Pusat",
        amount=Decimal("100.00"),
        debit_account_code="1-1101",
        credit_account_code="4-1101",
        reference="",
        mitra_id=None,
        created_by="u1",
        created_at=datetime(2026, 9, 1, 10, 0, 0),
        is_closing=False,
        proofs=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_session(*, total: int, rows: list):
    session = MagicMock()
    session.scalar = AsyncMock(return_value=total)
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


def _where_sql_fragments(session: MagicMock) -> str:
    """Collect SQL text from count (scalar) + list (execute) statements."""
    chunks: list[str] = []
    for call in session.scalar.await_args_list:
        stmt = call.args[0]
        chunks.append(str(stmt))
    for call in session.execute.await_args_list:
        stmt = call.args[0]
        chunks.append(str(stmt))
    return "\n".join(chunks)


@pytest.mark.asyncio
async def test_omit_unit_param_does_not_filter_unit():
    """Live clients that omit unit_usaha_id must still see all units."""
    rows = [_tx_row(id="tx-a", unit_usaha_id=None), _tx_row(id="tx-b", unit_usaha_id="u1")]
    session = _mock_session(total=2, rows=rows)
    user = SimpleNamespace(role="admin", unit_usaha_id=None)
    response = Response()

    result = await list_transactions(
        response=response,
        unit_usaha_id=None,
        meta=True,
        user=user,
        session=session,
        limit=50,
        offset=0,
    )

    assert result["total"] == 2
    sql = _where_sql_fragments(session)
    assert "unit_usaha_id" not in sql or "IS NULL" not in sql.upper()
    # Stronger: no equality/IS NULL on unit when omitted — count stmt has no WHERE unit.
    # Count is select(func.count()) with optional where; with no filters, no where clause.
    count_stmt = str(session.scalar.await_args.args[0])
    assert "WHERE" not in count_stmt.upper()


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel", ["", "__null__"])
async def test_pusat_sentinel_filters_is_null(sentinel: str):
    rows = [_tx_row(id="tx-pusat", unit_usaha_id=None)]
    session = _mock_session(total=1, rows=rows)
    user = SimpleNamespace(role="admin", unit_usaha_id=None)
    response = Response()

    result = await list_transactions(
        response=response,
        unit_usaha_id=sentinel,
        meta=True,
        user=user,
        session=session,
        limit=50,
        offset=0,
    )

    assert result["total"] == 1
    assert result["items"][0]["unit_usaha_id"] is None
    count_stmt = str(session.scalar.await_args.args[0])
    list_stmt = str(session.execute.await_args.args[0])
    combined = (count_stmt + "\n" + list_stmt).upper()
    assert "IS NULL" in combined
    assert "UNIT_USAHA_ID" in combined


@pytest.mark.asyncio
async def test_real_unit_id_still_equality_filter():
    rows = [_tx_row(id="tx-u", unit_usaha_id="unit-A")]
    session = _mock_session(total=1, rows=rows)
    user = SimpleNamespace(role="admin", unit_usaha_id=None)
    response = Response()

    result = await list_transactions(
        response=response,
        unit_usaha_id="unit-A",
        meta=True,
        user=user,
        session=session,
        limit=50,
        offset=0,
    )

    assert result["items"][0]["unit_usaha_id"] == "unit-A"
    combined = (
        str(session.scalar.await_args.args[0]) + "\n" + str(session.execute.await_args.args[0])
    ).upper()
    assert "IS NULL" not in combined
    assert "UNIT_USAHA_ID" in combined
