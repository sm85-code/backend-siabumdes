"""Stock-out untuk pemakaian/transfer internal (bukan penjualan).

Beda dari stock_out() biasa: tidak ada customer/harga jual, tidak membuat
record Sale (jadi tidak muncul di tab Piutang), dan cuma posting SATU
jurnal (beban pemakaian), bukan dua (HPP + Pendapatan) -- karena tidak ada
transaksi jual-beli dengan pihak luar.

Runs against a real (Postgres) async session, since Account/Transaction use
Postgres-only column types (JSONB/ARRAY) that don't work against SQLite.
Creates/drops only the tables it needs (same pattern as
test_arus_kas_internal_transfer.py) -- assumes it runs against an empty
database, not one already carrying the full production schema (which would
have unlisted tables holding dangling FKs into these, e.g.
inventory_purchases -> stock_cards).
"""
from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest

if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
    pytest.skip("Requires a real Postgres DATABASE_URL", allow_module_level=True)

import pytest_asyncio  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from modules.siabumdes.infrastructure.models import (  # noqa: E402
    Account,
    JournalEntry,
    JournalItem,
    Transaction,
    UnitUsaha,
)
from modules.siabumdes.inventory.application.services import InventoryService  # noqa: E402
from modules.siabumdes.inventory.infrastructure.models import (  # noqa: E402
    CommercialDocument,
    DocumentSource,
    Customer,
    Product,
    Purchase,
    PurchasePayment,
    Sale,
    SalePayment,
    StockCard,
    StockCategory,
    Vendor,
)
from shared.database import Base, DATABASE_URL as ASYNC_DATABASE_URL  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")
_TABLES = [
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
    CommercialDocument.__table__,
    DocumentSource.__table__,
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


async def _setup(session) -> dict:
    suffix = uuid.uuid4().hex[:8]
    unit = UnitUsaha(id=str(uuid.uuid4()), code=f"TST{suffix}", name="Toko Uji Internal")
    beban = Account(
        id=str(uuid.uuid4()), code=f"6.9.{suffix}", name="Beban Pemakaian Internal",
        category="beban", subcategory="beban_operasional", normal_balance="debit", group_code=unit.code,
    )
    persediaan = Account(
        id=str(uuid.uuid4()), code=f"1.1.{suffix}", name="Persediaan Barang Dagangan",
        category="aset", subcategory="aset_lancar", normal_balance="debit", group_code=unit.code,
    )
    session.add_all([unit, beban, persediaan])
    await session.flush()
    cat = StockCategory(id=str(uuid.uuid4()), code=f"UMUM-{suffix}", name="Umum", unit_usaha_id=unit.id)
    session.add(cat)
    await session.flush()
    product = Product(
        id=str(uuid.uuid4()), unit_usaha_id=unit.id, sku=f"INT-{suffix}", name="Produk Uji Internal",
        category_id=cat.id, qty_on_hand=20, cost_price=Decimal("10000"), sell_price=Decimal("15000"),
    )
    session.add(product)
    await session.flush()
    return {"unit": unit, "beban": beban, "persediaan": persediaan, "product": product}


@pytest.mark.asyncio
async def test_stock_out_internal_posts_single_journal_no_sale(session):
    ctx = await _setup(session)
    svc = InventoryService(session)

    card = await svc.stock_out_internal(
        product_id=ctx["product"].id,
        quantity=5,
        movement_date=date(2026, 9, 26),
        unit_usaha_id=ctx["unit"].id,
        debit_account_code=ctx["beban"].code,
        credit_account_code=ctx["persediaan"].code,
        note="Dipakai untuk operasional kantor",
    )
    await session.flush()

    await session.refresh(ctx["product"])
    assert ctx["product"].qty_on_hand == 15
    assert card.movement_kind == "internal_use"
    assert card.finance_status == "posted"
    assert card.total_value == Decimal("50000.00")

    tx_count = await session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.unit_usaha_id == ctx["unit"].id)
    )
    assert tx_count == 1, "harus cuma 1 jurnal (beban), bukan HPP+Pendapatan"

    tx = (
        await session.execute(select(Transaction).where(Transaction.unit_usaha_id == ctx["unit"].id))
    ).scalar_one()
    assert tx.debit_account_code == ctx["beban"].code
    assert tx.credit_account_code == ctx["persediaan"].code
    assert tx.amount == Decimal("50000.00")
    assert tx.transaction_type == "inventory_internal_use"

    sale = await session.scalar(select(Sale).where(Sale.stock_card_id == card.id))
    assert sale is None, "tidak boleh ada record Sale untuk pemakaian internal"


@pytest.mark.asyncio
async def test_stock_out_internal_insufficient_stock_rejected(session):
    ctx = await _setup(session)
    svc = InventoryService(session)

    with pytest.raises(ValueError, match="insufficient stock"):
        await svc.stock_out_internal(
            product_id=ctx["product"].id,
            quantity=999,
            movement_date=date(2026, 9, 26),
            unit_usaha_id=ctx["unit"].id,
            debit_account_code=ctx["beban"].code,
            credit_account_code=ctx["persediaan"].code,
        )


@pytest.mark.asyncio
async def test_cancel_movement_reverses_internal_use_cleanly(session):
    ctx = await _setup(session)
    svc = InventoryService(session)

    card = await svc.stock_out_internal(
        product_id=ctx["product"].id,
        quantity=5,
        movement_date=date(2026, 9, 26),
        unit_usaha_id=ctx["unit"].id,
        debit_account_code=ctx["beban"].code,
        credit_account_code=ctx["persediaan"].code,
    )
    await session.flush()
    card_id = card.id

    await svc.cancel_movement(card_id)
    await session.flush()

    await session.refresh(ctx["product"])
    assert ctx["product"].qty_on_hand == 20, "qty harus balik seperti semula"

    remaining_card = await session.scalar(select(StockCard).where(StockCard.id == card_id))
    assert remaining_card is None

    tx_count = await session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.unit_usaha_id == ctx["unit"].id)
    )
    assert tx_count == 0
