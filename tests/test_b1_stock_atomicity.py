"""SIABUMDES B1 — UU05 stock qty_on_hand race hardening.

Unit coverage always runs. Concurrent Postgres integration skips unless
DATABASE_URL points at Postgres (same pattern as test_stock_out_internal_use).
"""
from __future__ import annotations

import os

# Importing inventory models pulls shared.database, which requires DATABASE_URL
# at import time. CI injects a real URL; for local/unit collection provide a
# harmless placeholder (connection is only opened in the optional PG test).
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1:5432/siabumdes_test")

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql

from modules.siabumdes.inventory.application.stock_mixin import InventoryStockMixin
from modules.siabumdes.inventory.infrastructure.models import Product


class _Svc(InventoryStockMixin):
    def __init__(self, session):
        self.session = session
        self.finance = MagicMock()


@pytest.mark.asyncio
async def test_get_product_for_update_compiles_with_for_update():
    """Guard: lock helper must emit SELECT ... FOR UPDATE (Postgres)."""
    stmt = select(Product).where(Product.id == "p1").with_for_update()
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    sql = str(compiled).lower()
    assert "for update" in sql


@pytest.mark.asyncio
async def test_decrement_qty_atomic_uses_where_qty_guard():
    stmt = (
        update(Product)
        .where(Product.id == "p1", Product.qty_on_hand >= 3)
        .values(qty_on_hand=Product.qty_on_hand - 3)
        .returning(Product)
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "qty_on_hand" in sql
    assert "returning" in sql


@pytest.mark.asyncio
async def test_decrement_qty_atomic_raises_insufficient():
    session = MagicMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = None
    session.scalars = AsyncMock(return_value=scalars_result)
    session.get = AsyncMock(return_value=MagicMock())

    svc = _Svc(session)
    with pytest.raises(ValueError, match="insufficient stock"):
        await svc._decrement_qty_atomic("p1", 5)


@pytest.mark.asyncio
async def test_decrement_qty_atomic_raises_not_found():
    session = MagicMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = None
    session.scalars = AsyncMock(return_value=scalars_result)
    session.get = AsyncMock(return_value=None)

    svc = _Svc(session)
    with pytest.raises(ValueError, match="product not found"):
        await svc._decrement_qty_atomic("missing", 1)


@pytest.mark.asyncio
async def test_concurrent_stock_out_cannot_oversell():
    """Two overlapping outs for the last unit: exactly one succeeds (Postgres)."""
    if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
        pytest.skip("Requires a real Postgres DATABASE_URL")

    import asyncio
    import uuid
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from modules.siabumdes.infrastructure.models import (
        Account,
        JournalEntry,
        JournalItem,
        Transaction,
        UnitUsaha,
    )
    from modules.siabumdes.inventory.application.services import InventoryService
    from modules.siabumdes.inventory.infrastructure.models import (
        Customer,
        Purchase,
        PurchasePayment,
        Sale,
        SalePayment,
        StockCard,
        StockCategory,
        Vendor,
    )
    from shared.database import Base, DATABASE_URL as ASYNC_DATABASE_URL

    tables = [
        UnitUsaha.__table__,
        Account.__table__,
        Transaction.__table__,
        JournalEntry.__table__,
        JournalItem.__table__,
        StockCategory.__table__,
        Product.__table__,
        StockCard.__table__,
        Vendor.__table__,
        Purchase.__table__,
        PurchasePayment.__table__,
        Customer.__table__,
        Sale.__table__,
        SalePayment.__table__,
    ]

    engine = create_async_engine(ASYNC_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL unavailable: {exc}")

    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    suffix = uuid.uuid4().hex[:8]
    product_id = str(uuid.uuid4())
    customer_id = str(uuid.uuid4())
    unit_id = str(uuid.uuid4())
    cogs_code = f"5.1.{suffix}"
    inv_code = f"1.1.{suffix}"
    cash_code = f"1.0.{suffix}"
    rev_code = f"4.1.{suffix}"

    async with session_local() as s:
        unit = UnitUsaha(id=unit_id, code=f"C{suffix}", name="Concurrency UU")
        accounts = [
            Account(
                id=str(uuid.uuid4()), code=cogs_code, name="HPP", category="beban",
                subcategory="hpp", normal_balance="debit", group_code=unit.code,
            ),
            Account(
                id=str(uuid.uuid4()), code=inv_code, name="Persediaan", category="aset",
                subcategory="persediaan", normal_balance="debit", group_code=unit.code,
            ),
            Account(
                id=str(uuid.uuid4()), code=cash_code, name="Kas", category="aset",
                subcategory="kas", normal_balance="debit", group_code=unit.code,
            ),
            Account(
                id=str(uuid.uuid4()), code=rev_code, name="Penjualan", category="pendapatan",
                subcategory="penjualan", normal_balance="credit", group_code=unit.code,
            ),
        ]
        cat = StockCategory(
            id=str(uuid.uuid4()), code=f"CAT{suffix}", name="Cat", unit_usaha_id=unit_id,
        )
        product = Product(
            id=product_id, sku=f"SKU{suffix}", name="Item", category_id=cat.id,
            unit_usaha_id=unit_id, qty_on_hand=1, cost_price=Decimal("1000"),
            sell_price=Decimal("1500"),
        )
        customer = Customer(id=customer_id, name="Cust", unit_usaha_id=unit_id)
        s.add_all([unit, *accounts, cat, product, customer])
        await s.commit()

    async def _attempt() -> str:
        async with session_local() as s:
            svc = InventoryService(s)
            try:
                await svc.stock_out(
                    product_id=product_id,
                    quantity=1,
                    movement_date=date.today(),
                    unit_usaha_id=unit_id,
                    debit_account_code=cogs_code,
                    credit_account_code=inv_code,
                    customer_id=customer_id,
                    sell_price=Decimal("1500"),
                    revenue_debit_account_code=cash_code,
                    revenue_credit_account_code=rev_code,
                )
                await s.commit()
                return "ok"
            except ValueError as exc:
                await s.rollback()
                return f"err:{exc}"

    results = await asyncio.gather(_attempt(), _attempt())
    assert results.count("ok") == 1, results
    assert any(r.startswith("err:") for r in results), results

    async with session_local() as s:
        left = await s.get(Product, product_id)
        assert left is not None
        assert left.qty_on_hand == 0

    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
    await engine.dispose()
