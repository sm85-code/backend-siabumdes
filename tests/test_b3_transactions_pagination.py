"""SIABUMDES B3 — GET /api/transactions real pagination (limit/offset + meta).

Unit tests (no Postgres required). DATABASE_URL placeholder for model import.
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

from modules.siabumdes.adapters.api.v1.transaction_router import (
    DEFAULT_TX_LIMIT,
    MAX_TX_LIMIT,
    _apply_tx_list_headers,
    _tx_out,
    _tx_page_meta,
    list_transactions,
)


def _tx_row(**overrides):
    base = dict(
        id="tx-1",
        date=date(2026, 9, 1),
        unit_usaha_id=None,
        transaction_type="pendapatan",
        description="Test",
        amount=Decimal("1500.50"),
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


def test_tx_page_meta_has_more_when_more_remain():
    page = _tx_page_meta(total=12, limit=5, offset=0, item_count=5)
    assert page == {"total": 12, "limit": 5, "offset": 0, "has_more": True}


def test_tx_page_meta_no_more_on_last_page():
    page = _tx_page_meta(total=12, limit=5, offset=10, item_count=2)
    assert page["has_more"] is False
    assert page["total"] == 12


def test_tx_page_meta_empty():
    page = _tx_page_meta(total=0, limit=500, offset=0, item_count=0)
    assert page["has_more"] is False


def test_apply_tx_list_headers():
    response = Response()
    _apply_tx_list_headers(
        response, {"total": 42, "limit": 10, "offset": 20, "has_more": True}
    )
    assert response.headers["X-Total-Count"] == "42"
    assert response.headers["X-Limit"] == "10"
    assert response.headers["X-Offset"] == "20"
    assert response.headers["X-Has-More"] == "true"


def test_tx_out_amount_is_decimal_string():
    """Money JSON: amount is Decimal→str (TS FE parseMoney accepts number|string)."""
    out = _tx_out(_tx_row())
    assert isinstance(out["amount"], str)
    assert out["amount"] == "1500.50"


def test_default_and_max_limits():
    assert DEFAULT_TX_LIMIT == 500
    assert MAX_TX_LIMIT == 2000


def _mock_session(*, total: int, rows: list):
    session = MagicMock()
    session.scalar = AsyncMock(return_value=total)
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_list_transactions_legacy_array_body():
    rows = [_tx_row(id=f"tx-{i}") for i in range(3)]
    session = _mock_session(total=3, rows=rows)
    user = SimpleNamespace(role="admin", unit_usaha_id=None)
    response = Response()

    result = await list_transactions(
        response=response,
        start_date=None,
        end_date=None,
        unit_usaha_id=None,
        reference=None,
        limit=500,
        offset=0,
        meta=False,
        user=user,
        session=session,
    )

    assert isinstance(result, list)
    assert len(result) == 3
    assert result[0]["id"] == "tx-0"
    assert response.headers["X-Total-Count"] == "3"
    assert response.headers["X-Has-More"] == "false"
    assert response.headers["X-Limit"] == "500"
    assert response.headers["X-Offset"] == "0"


@pytest.mark.asyncio
async def test_list_transactions_meta_envelope_and_has_more():
    rows = [_tx_row(id=f"tx-{i}") for i in range(5)]
    session = _mock_session(total=12, rows=rows)
    user = SimpleNamespace(role="admin", unit_usaha_id=None)
    response = Response()

    result = await list_transactions(
        response=response,
        start_date=None,
        end_date=None,
        unit_usaha_id=None,
        reference=None,
        limit=5,
        offset=0,
        meta=True,
        user=user,
        session=session,
    )

    assert isinstance(result, dict)
    assert [x["id"] for x in result["items"]] == [f"tx-{i}" for i in range(5)]
    assert result["total"] == 12
    assert result["limit"] == 5
    assert result["offset"] == 0
    assert result["has_more"] is True
    assert response.headers["X-Total-Count"] == "12"
    assert response.headers["X-Has-More"] == "true"


@pytest.mark.asyncio
async def test_list_transactions_offset_second_page():
    rows = [_tx_row(id="tx-last")]
    session = _mock_session(total=6, rows=rows)
    user = SimpleNamespace(role="admin", unit_usaha_id=None)
    response = Response()

    result = await list_transactions(
        response=response,
        limit=5,
        offset=5,
        meta=True,
        user=user,
        session=session,
    )

    assert result["items"][0]["id"] == "tx-last"
    assert result["offset"] == 5
    assert result["has_more"] is False
    assert response.headers["X-Offset"] == "5"
    assert response.headers["X-Has-More"] == "false"

    # Data query must use offset/limit (count is scalar).
    assert session.scalar.await_count == 1
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_list_transactions_pengelola_scoped_count_and_list():
    """Pengelola filter must apply to both count and list queries."""
    rows = [_tx_row(id="tx-u", unit_usaha_id="unit-A")]
    session = _mock_session(total=1, rows=rows)
    user = SimpleNamespace(role="pengelola", unit_usaha_id="unit-A")
    response = Response()

    result = await list_transactions(
        response=response,
        meta=True,
        user=user,
        session=session,
        limit=50,
        offset=0,
    )

    assert result["total"] == 1
    assert result["items"][0]["unit_usaha_id"] == "unit-A"
    # Ensure we issued a count + a select (both go through session).
    assert session.scalar.await_count == 1
    assert session.execute.await_count == 1
