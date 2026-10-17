"""Nomor nota otomatis Stock In/Out: format, urutan per dokumen mingguan, tidak dipakai ulang,
dan aman saat disimpan bersamaan (tanpa tabrakan)."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest

if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
    pytest.skip("Requires a real Postgres DATABASE_URL", allow_module_level=True)

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from modules.siabumdes.infrastructure.models import (  # noqa: E402
    Account, JournalEntry, JournalItem, Transaction, UnitUsaha,
)
from modules.siabumdes.inventory.application.receipt_number import (  # noqa: E402
    backfill_receipt_numbers, preview_receipt_number,
)
from modules.siabumdes.inventory.application.services import InventoryService  # noqa: E402
from modules.siabumdes.inventory.infrastructure.models import (  # noqa: E402
    CommercialDocument, Customer, DocNumber, DocumentSource, Product, Purchase, PurchasePayment,
    ReceiptCounter, Sale, SalePayment, StockCard, StockCategory, Vendor,
)
from shared.database import Base, DATABASE_URL as ASYNC_DATABASE_URL  # noqa: E402

_TABLES = [
    UnitUsaha.__table__, Account.__table__, Transaction.__table__, JournalEntry.__table__,
    JournalItem.__table__, StockCategory.__table__, Product.__table__, StockCard.__table__,
    Vendor.__table__, Purchase.__table__, PurchasePayment.__table__, Customer.__table__,
    Sale.__table__, SalePayment.__table__, CommercialDocument.__table__, DocumentSource.__table__,
    DocNumber.__table__, ReceiptCounter.__table__,
]
D = date(2026, 10, 6)  # Selasa, minggu ke-1 Oktober 2026 (Senin 5 Okt)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(ASYNC_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=_TABLES))
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=_TABLES))
    await engine.dispose()


async def _setup(session) -> dict:
    unit = UnitUsaha(id=str(uuid.uuid4()), code="UU05", name="Toko Uji")
    session.add(unit)
    codes = {"pers": "1.1.05.01", "kas": "1.1.01.01", "hpp": "5.1.01", "pend": "4.1.01", "beban": "6.1.01",
             "ma_d": "1.1.05.15", "ma_k": "1.1.01.15"}
    for code in codes.values():
        session.add(Account(id=str(uuid.uuid4()), code=code, name=code, category="aset",
                            subcategory="aset_lancar", normal_balance="debit", group_code="UU05"))
    await session.flush()
    cat = StockCategory(id=str(uuid.uuid4()), code="UMUM", name="Umum", unit_usaha_id=unit.id)
    session.add(cat)
    await session.flush()
    product = Product(id=str(uuid.uuid4()), unit_usaha_id=unit.id, sku="KRS.UMUM-1", name="Beras",
                      category_id=cat.id, qty_on_hand=100, cost_price=Decimal("1000"), sell_price=Decimal("1500"))
    vendor = Vendor(id=str(uuid.uuid4()), unit_usaha_id=unit.id, name="Budi", code="BUD")
    customer = Customer(id=str(uuid.uuid4()), unit_usaha_id=unit.id, name="Ani", code="ANI")
    session.add_all([product, vendor, customer])
    await session.flush()
    return {"unit": unit.id, "product": product.id, "vendor": vendor.id, "customer": customer.id, **codes}


def _in(ctx, **kw):
    return dict(lines=[{"product_id": ctx["product"], "quantity": 1, "unit_cost": Decimal("1000")}],
                movement_date=D, unit_usaha_id=ctx["unit"], debit_account_code=ctx["pers"],
                credit_account_code=ctx["kas"], vendor_id=ctx["vendor"], **kw)


@pytest.mark.asyncio
async def test_numbering_format_sequence_and_no_reuse(factory):
    async with factory() as s:
        ctx = await _setup(s)
        svc = InventoryService(s)
        preview = await preview_receipt_number(s, kind="po", unit_id=ctx["unit"], partner_id=ctx["vendor"], d=D)
        a = (await svc.stock_in_nota(**_in(ctx)))[0]
        b = (await svc.stock_in_nota(**_in(ctx)))[0]
        assert preview == a.doc_number == "PO-BUD-M1-001/X/2026-1"
        assert b.doc_number == "PO-BUD-M1-001/X/2026-2"
        # Batal tidak membuat nomor dipakai ulang.
        await svc.cancel_movement(b.id)
        c = (await svc.stock_in_nota(**_in(ctx)))[0]
        assert c.doc_number == "PO-BUD-M1-001/X/2026-3"
        out = (await svc.stock_out_nota(
            lines=[{"product_id": ctx["product"], "quantity": 1, "sell_price": Decimal("1500")}],
            movement_date=D, unit_usaha_id=ctx["unit"], debit_account_code=ctx["hpp"],
            credit_account_code=ctx["pers"], customer_id=ctx["customer"],
            revenue_debit_account_code=ctx["kas"], revenue_credit_account_code=ctx["pend"]))[0]
        assert out.doc_number == "INV-ANI-M1-001/X/2026-1"
        ma = (await svc.stock_in_nota(**{**_in(ctx), "vendor_id": None, "payment_method": "modal_awal"}))[0]
        assert ma.doc_number == "MA-M1/X/2026-1"
        internal = await svc.stock_out_internal(
            product_id=ctx["product"], quantity=1, movement_date=D, unit_usaha_id=ctx["unit"],
            debit_account_code=ctx["beban"], credit_account_code=ctx["pers"])
        assert internal.doc_number == "INT-M1/X/2026-1"
        # Nota lama tanpa nomor diisi backfill, melanjutkan penghitung (tidak bertabrakan).
        a.doc_number = None
        a.doc_unit_id = None
        await s.commit()
    assert await backfill_receipt_numbers(factory) == 1
    async with factory() as s:
        assert (await s.get(StockCard, a.id)).doc_number == "PO-BUD-M1-001/X/2026-4"


@pytest.mark.asyncio
async def test_concurrent_saves_get_distinct_numbers(factory):
    async with factory() as s:
        ctx = await _setup(s)
        # Produk berbeda per nota supaya kunci baris produk tidak ikut menserialkan penyimpanan.
        products = [Product(id=str(uuid.uuid4()), unit_usaha_id=ctx["unit"], sku=f"KRS.UMUM-P{i}", name=f"P{i}",
                            category_id=(await s.get(Product, ctx["product"])).category_id) for i in range(8)]
        s.add_all(products)
        await s.commit()

    async def save(i):
        async with factory() as s:
            card = (await InventoryService(s).stock_in_nota(**{**_in(ctx), "lines": [
                {"product_id": products[i].id, "quantity": 1, "unit_cost": Decimal("1000")}]}))[0]
            await s.commit()
            return card.doc_number

    numbers = await asyncio.gather(*[save(i) for i in range(8)])
    assert sorted(numbers, key=lambda n: int(n.rsplit("-", 1)[1])) == [
        f"PO-BUD-M1-001/X/2026-{i}" for i in range(1, 9)
    ]
