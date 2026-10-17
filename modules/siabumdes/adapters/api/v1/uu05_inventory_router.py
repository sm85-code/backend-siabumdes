"""UU05 inventory HTTP API (/api/v1/uu05_inventory)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.adapters.api.scope import assert_can_mutate_period, assert_unit_active
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.infrastructure.models import UnitUsaha
from modules.siabumdes.inventory.application.services import (
    InventoryService,
    INVENTORY_BUSINESS_TYPES,
    UU05_CODE,
)
from modules.siabumdes.inventory.infrastructure.models import (
    Product,
    Purchase,
    PurchasePayment,
    Sale,
    SalePayment,
    StockAdjustment,
    StockCard,
)
from shared.config import public_role
from shared.database import get_db

router = APIRouter(prefix="/api/v1/uu05_inventory", tags=["uu05_inventory"])


async def _assert_card_periods(session: AsyncSession, user: User, stock_card_id: str) -> None:
    """Pembatalan stok menghapus jurnal di tanggal asli pergerakan + pelunasannya:
    semua tanggal itu harus berada di periode yang masih terbuka."""
    card = await session.get(StockCard, stock_card_id)
    if not card:
        return
    product = await session.get(Product, card.product_id)
    uid = product.unit_usaha_id if product else None
    dates = {card.movement_date}
    if getattr(card, "nota_id", None):
        dates.update(await session.scalars(select(StockCard.movement_date).where(StockCard.nota_id == card.nota_id)))
        card = await session.get(StockCard, card.nota_id) or card
    dates.update(await session.scalars(
        select(PurchasePayment.paid_date).join(Purchase, PurchasePayment.purchase_id == Purchase.id)
        .where(Purchase.stock_card_id == card.id)))
    dates.update(await session.scalars(
        select(SalePayment.paid_date).join(Sale, SalePayment.sale_id == Sale.id)
        .where(Sale.stock_card_id == card.id)))
    for d in sorted(dates):
        await assert_can_mutate_period(session, user, d, uid)


async def _assert_adjustment_period(session: AsyncSession, user: User, adjustment_id: str) -> None:
    row = await session.get(StockAdjustment, adjustment_id)
    if not row:
        return
    product = await session.get(Product, row.product_id)
    await assert_can_mutate_period(session, user, row.adjustment_date, product.unit_usaha_id if product else None)

INVENTORY_ROLES = ("admin", "direktur", "bendahara", "pengelola")


async def require_inventory_user(
    user: User = Depends(require_roles(*INVENTORY_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> User:
    if public_role(user.role) != "pengelola":
        return user
    if not user.unit_usaha_id:
        raise HTTPException(status_code=403, detail="Pengelola belum terikat unit usaha")
    unit = await session.get(UnitUsaha, user.unit_usaha_id)
    if not unit or unit.business_type not in INVENTORY_BUSINESS_TYPES:
        raise HTTPException(status_code=403, detail="Inventory hanya untuk unit usaha Perdagangan/Manufaktur")
    return user


async def require_active_inventory_writer(
    user: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Same access as require_inventory_user, plus: a pengelola whose own unit
    has been deactivated by admin can still log in and view everything (read-
    only), but cannot record/edit/cancel anything new. Used on every mutating
    (POST/PUT/DELETE) endpoint in this router instead of require_inventory_user."""
    if public_role(user.role) == "pengelola" and user.unit_usaha_id:
        await assert_unit_active(session, user.unit_usaha_id)
    return user


def card_out(card) -> dict:
    return {
        "id": card.id,
        "nota_id": card.nota_id or card.id,
        "doc_number": card.doc_number or "",
        "reference": card.reference,
        "finance_status": card.finance_status,
        "quantity": card.quantity,
        "total_value": str(card.total_value),
        "direction": card.direction,
        "movement_kind": card.movement_kind,
        "product_id": card.product_id,
        "movement_date": card.movement_date.isoformat() if card.movement_date else None,
        "unit_cost": str(card.unit_cost),
    }


class StockInBody(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)
    movement_date: date
    unit_usaha_id: Optional[str] = None
    debit_account_code: str
    credit_account_code: str
    vendor_id: Optional[str] = None
    invoice_number: str = ""
    payment_method: str = "cash"
    due_date: Optional[date] = None


class StockOutBody(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    movement_date: date
    unit_usaha_id: Optional[str] = None
    debit_account_code: str
    credit_account_code: str
    customer_id: str
    sell_price: Decimal = Field(ge=0)
    revenue_debit_account_code: str
    revenue_credit_account_code: str
    invoice_number: str = ""
    payment_method: str = "cash"
    due_date: Optional[date] = None


class StockInLine(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)


class StockInNotaBody(BaseModel):
    lines: list[StockInLine] = Field(min_length=1, max_length=100)
    movement_date: date
    unit_usaha_id: Optional[str] = None
    debit_account_code: str
    credit_account_code: str
    vendor_id: Optional[str] = None
    invoice_number: str = ""
    payment_method: str = "cash"
    due_date: Optional[date] = None


class StockOutLine(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    sell_price: Decimal = Field(ge=0)


class StockOutNotaBody(BaseModel):
    lines: list[StockOutLine] = Field(min_length=1, max_length=100)
    movement_date: date
    unit_usaha_id: Optional[str] = None
    debit_account_code: str
    credit_account_code: str
    customer_id: str
    revenue_debit_account_code: str
    revenue_credit_account_code: str
    invoice_number: str = ""
    payment_method: str = "cash"
    due_date: Optional[date] = None


class StockOutInternalBody(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    movement_date: date
    unit_usaha_id: Optional[str] = None
    debit_account_code: str
    credit_account_code: str
    note: str = ""


class VendorCreateBody(BaseModel):
    name: str
    unit_usaha_id: Optional[str] = None
    contact: Optional[str] = None
    address: Optional[str] = None


class VendorUpdateBody(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None


class CustomerCreateBody(BaseModel):
    name: str
    unit_usaha_id: Optional[str] = None
    contact: Optional[str] = None
    address: Optional[str] = None


class CustomerUpdateBody(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None


class PayPurchaseBody(BaseModel):
    purchase_id: str
    amount: Decimal = Field(gt=0)
    paid_date: date
    unit_usaha_id: Optional[str] = None
    debit_account_code: str
    credit_account_code: str


class PaySaleBody(BaseModel):
    sale_id: str
    amount: Decimal = Field(gt=0)
    paid_date: date
    unit_usaha_id: Optional[str] = None
    debit_account_code: str
    credit_account_code: str


class CancelBody(BaseModel):
    stock_card_id: str


class CancelAdjustBody(BaseModel):
    adjustment_id: str


class ProductCreateBody(BaseModel):
    sku: str
    name: str
    category_id: str
    unit_usaha_id: Optional[str] = None
    unit_of_measure: str = "pcs"
    cost_price: Decimal = Field(default=Decimal("0"), ge=0)
    sell_price: Decimal = Field(default=Decimal("0"), ge=0)
    opening_qty: int = Field(default=0, ge=0)


class ProductUpdateBody(BaseModel):
    name: Optional[str] = None
    category_id: Optional[str] = None
    unit_of_measure: Optional[str] = None
    cost_price: Optional[Decimal] = Field(default=None, ge=0)
    sell_price: Optional[Decimal] = Field(default=None, ge=0)


class AdjustBody(BaseModel):
    product_id: str
    quantity_delta: int
    reason: str
    adjustment_date: date
    notes: Optional[str] = None
    unit_usaha_id: Optional[str] = None
    debit_account_code: Optional[str] = None
    credit_account_code: Optional[str] = None


async def resolve_unit_id(session: AsyncSession, body_unit_id: Optional[str], user: User) -> str:
    if public_role(user.role) == "pengelola" and user.unit_usaha_id:
        uid = user.unit_usaha_id
    elif body_unit_id:
        uid = body_unit_id
    else:
        unit = await session.scalar(
            select(UnitUsaha).where(UnitUsaha.business_type.in_(INVENTORY_BUSINESS_TYPES)).order_by(UnitUsaha.code.asc())
        )
        if not unit:
            raise HTTPException(status_code=422, detail="Belum ada unit usaha berjenis Perdagangan/Manufaktur")
        uid = unit.id
    # Semua endpoint pencatatan inventory lewat sini -- cek sekali di sini
    # cukup untuk mencegah stok in/out/penyesuaian baru dicatat ke unit yang
    # sudah dinonaktifkan admin.
    await assert_unit_active(session, uid)
    return uid


@router.get("/meta")
async def inventory_meta(
    user: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    unit = await session.scalar(
        select(UnitUsaha).where(UnitUsaha.business_type.in_(INVENTORY_BUSINESS_TYPES)).order_by(UnitUsaha.code.asc())
    )
    svc = InventoryService(session)
    return {
        "unit_code": unit.code if unit else UU05_CODE,
        "unit_usaha_id": unit.id if unit else None,
        "unit_name": unit.name if unit else None,
        "role": public_role(user.role),
        "categories": await svc.list_categories(),
    }


@router.get("/categories")
async def list_categories(
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).list_categories()


class CategoryBody(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=500)


class CategoryPatch(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=32)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=500)


@router.post("/categories")
async def create_category(
    body: CategoryBody,
    _: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await InventoryService(session).create_category(code=body.code, name=body.name, description=body.description)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/categories/{category_id}")
async def update_category(
    category_id: str,
    body: CategoryPatch,
    _: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await InventoryService(session).update_category(category_id, code=body.code, name=body.name, description=body.description)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    _: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    try:
        await InventoryService(session).delete_category(category_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"deleted": category_id}


@router.get("/products")
async def list_products(
    category_id: Optional[str] = None,
    q: Optional[str] = None,
    unit_usaha_id: Optional[str] = None,
    user: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    uid = user.unit_usaha_id if public_role(user.role) == "pengelola" else unit_usaha_id
    return await InventoryService(session).list_products(
        unit_usaha_id=uid, category_id=category_id, q=q
    )


@router.post("/products")
async def create_product(
    body: ProductCreateBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    svc = InventoryService(session)
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        return await svc.create_product(
            sku=body.sku,
            name=body.name,
            category_id=body.category_id,
            unit_usaha_id=uid,
            unit_of_measure=body.unit_of_measure,
            cost_price=body.cost_price,
            sell_price=body.sell_price,
            opening_qty=body.opening_qty,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/products/{product_id}")
async def update_product(
    product_id: str,
    body: ProductUpdateBody,
    _: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await InventoryService(session).update_product(
            product_id,
            name=body.name,
            category_id=body.category_id,
            unit_of_measure=body.unit_of_measure,
            cost_price=body.cost_price,
            sell_price=body.sell_price,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class ProductSkuBody(BaseModel):
    category_id: str
    suffix: str = Field(min_length=1, max_length=60)


@router.put("/products/{product_id}/sku")
async def change_product_sku(
    product_id: str,
    body: ProductSkuBody,
    _: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    """Ganti kategori + SKU produk (juga produk yang sudah punya riwayat)."""
    try:
        return await InventoryService(session).change_category_sku(product_id, category_id=body.category_id, suffix=body.suffix)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: str,
    _: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        await InventoryService(session).delete_product(product_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"deleted": product_id}


@router.post("/stock-in")
async def stock_in(
    body: StockInBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    svc = InventoryService(session)
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        await assert_can_mutate_period(session, user, body.movement_date, uid)
        card = await svc.stock_in(
            product_id=body.product_id,
            quantity=body.quantity,
            unit_cost=body.unit_cost,
            movement_date=body.movement_date,
            unit_usaha_id=uid,
            debit_account_code=body.debit_account_code,
            credit_account_code=body.credit_account_code,
            vendor_id=body.vendor_id,
            invoice_number=body.invoice_number,
            payment_method=body.payment_method,
            due_date=body.due_date,
            created_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return card_out(card)


@router.post("/stock-in-nota")
async def stock_in_nota(
    body: StockInNotaBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    """Satu nota pembelian banyak barang; semua baris tersimpan atau tidak sama sekali."""
    svc = InventoryService(session)
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        await assert_can_mutate_period(session, user, body.movement_date, uid)
        data = body.model_dump(exclude={"lines", "unit_usaha_id"})
        cards = await svc.stock_in_nota(
            lines=[ln.model_dump() for ln in body.lines], unit_usaha_id=uid, created_by=user.username, **data
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"nota_id": cards[0].id, "cards": [card_out(c) for c in cards]}


@router.post("/stock-out-nota")
async def stock_out_nota(
    body: StockOutNotaBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    """Satu nota penjualan banyak barang; semua baris tersimpan atau tidak sama sekali."""
    svc = InventoryService(session)
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        await assert_can_mutate_period(session, user, body.movement_date, uid)
        data = body.model_dump(exclude={"lines", "unit_usaha_id"})
        cards = await svc.stock_out_nota(
            lines=[ln.model_dump() for ln in body.lines], unit_usaha_id=uid, created_by=user.username, **data
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"nota_id": cards[0].id, "cards": [card_out(c) for c in cards]}


@router.post("/stock-out-internal")
async def stock_out_internal(
    body: StockOutInternalBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    svc = InventoryService(session)
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        await assert_can_mutate_period(session, user, body.movement_date, uid)
        card = await svc.stock_out_internal(
            product_id=body.product_id,
            quantity=body.quantity,
            movement_date=body.movement_date,
            unit_usaha_id=uid,
            debit_account_code=body.debit_account_code,
            credit_account_code=body.credit_account_code,
            note=body.note,
            created_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return card_out(card)


@router.post("/stock-out")
async def stock_out(
    body: StockOutBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    svc = InventoryService(session)
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        await assert_can_mutate_period(session, user, body.movement_date, uid)
        card = await svc.stock_out(
            product_id=body.product_id,
            quantity=body.quantity,
            movement_date=body.movement_date,
            unit_usaha_id=uid,
            debit_account_code=body.debit_account_code,
            credit_account_code=body.credit_account_code,
            customer_id=body.customer_id,
            sell_price=body.sell_price,
            revenue_debit_account_code=body.revenue_debit_account_code,
            revenue_credit_account_code=body.revenue_credit_account_code,
            invoice_number=body.invoice_number,
            payment_method=body.payment_method,
            due_date=body.due_date,
            created_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return card_out(card)


@router.get("/doc-number-preview")
async def doc_number_preview(
    kind: Literal["po", "invoice", "ma", "int"],
    movement_date: date,
    partner_id: Optional[str] = None,
    unit_usaha_id: Optional[str] = None,
    user: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    """Perkiraan No. Nota berikutnya (tidak memesan nomor; nomor final diberikan saat simpan)."""
    from modules.siabumdes.inventory.application.receipt_number import preview_receipt_number

    if public_role(user.role) == "pengelola" and user.unit_usaha_id:
        uid = user.unit_usaha_id
    elif unit_usaha_id:
        uid = unit_usaha_id
    else:
        unit = await session.scalar(
            select(UnitUsaha).where(UnitUsaha.business_type.in_(INVENTORY_BUSINESS_TYPES)).order_by(UnitUsaha.code.asc())
        )
        if not unit:
            raise HTTPException(status_code=422, detail="Belum ada unit usaha berjenis Perdagangan/Manufaktur")
        uid = unit.id
    try:
        number = await preview_receipt_number(session, kind=kind, unit_id=uid, partner_id=partner_id, d=movement_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"doc_number": number, "estimated": True}


@router.post("/cancel-movement")
async def cancel_movement(
    body: CancelBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    await _assert_card_periods(session, user, body.stock_card_id)
    await InventoryService(session).cancel_movement(body.stock_card_id)
    return {"cancelled": body.stock_card_id}


@router.get("/movements")
async def list_movements(
    product_id: Optional[str] = None,
    direction: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(100, ge=1, le=2000),
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).list_movements(
        product_id=product_id, direction=direction, limit=limit,
        date_from=date_from, date_to=date_to,
    )



@router.post("/cancel-adjustment")
async def cancel_adjustment(
    body: CancelAdjustBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    await _assert_adjustment_period(session, user, body.adjustment_id)
    await InventoryService(session).cancel_adjustment(body.adjustment_id)
    return {"cancelled": body.adjustment_id}


@router.get("/adjustments")
async def list_adjustments(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(100, ge=1, le=2000),
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).list_adjustments(
        limit=limit, date_from=date_from, date_to=date_to
    )


@router.post("/adjustments")
async def create_adjustment(
    body: AdjustBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        await assert_can_mutate_period(session, user, body.adjustment_date, uid)
        return await InventoryService(session).adjust_stock(
            product_id=body.product_id,
            quantity_delta=body.quantity_delta,
            reason=body.reason,
            adjustment_date=body.adjustment_date,
            notes=body.notes,
            unit_usaha_id=uid,
            debit_account_code=body.debit_account_code,
            credit_account_code=body.credit_account_code,
            created_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/reports/valuation")
async def report_valuation(
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).stock_valuation_report()


@router.get("/reports/valuation-as-of")
async def report_valuation_as_of(
    dates: list[date] = Query(..., max_length=12),
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    """Nilai persediaan (qty × HPP produk saat ini) per tanggal, dimundurkan dengan membatalkan
    kartu stok/penyesuaian sesudah tanggal itu; sama dengan Stok di Neraca Manajemen Toko."""
    from modules.siabumdes.toko.application.neraca import Data, load_stock, stok_manajemen

    data = await load_stock(session, Data())
    return {"today": str(stok_manajemen(data)), "values": [{"date": d.isoformat(), "value": str(stok_manajemen(data, d))} for d in dates]}


@router.get("/reports/remaining-by-source")
async def report_remaining_by_source(
    as_of: Optional[date] = None,
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).remaining_by_source_report(as_of=as_of)


@router.get("/reports/movements")
async def report_movements(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).movement_summary_report(
        date_from=date_from, date_to=date_to
    )


@router.get("/vendors")
async def list_vendors(
    user: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    uid = user.unit_usaha_id if public_role(user.role) == "pengelola" else None
    return await InventoryService(session).list_vendors(unit_usaha_id=uid)


@router.post("/vendors")
async def create_vendor(
    body: VendorCreateBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        return await InventoryService(session).create_vendor(
            unit_usaha_id=uid, name=body.name, contact=body.contact, address=body.address
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/vendors/{vendor_id}")
async def update_vendor(
    vendor_id: str,
    body: VendorUpdateBody,
    _: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await InventoryService(session).update_vendor(
            vendor_id, name=body.name, contact=body.contact, address=body.address, is_active=body.is_active
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/customers")
async def list_customers(
    user: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    uid = user.unit_usaha_id if public_role(user.role) == "pengelola" else None
    return await InventoryService(session).list_customers(unit_usaha_id=uid)


@router.post("/customers")
async def create_customer(
    body: CustomerCreateBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        return await InventoryService(session).create_customer(
            unit_usaha_id=uid, name=body.name, contact=body.contact, address=body.address
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/customers/{customer_id}")
async def update_customer(
    customer_id: str,
    body: CustomerUpdateBody,
    _: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await InventoryService(session).update_customer(
            customer_id, name=body.name, contact=body.contact, address=body.address, is_active=body.is_active
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/purchases")
async def list_purchases(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).list_purchases(status=status, limit=limit)


@router.post("/purchases/pay")
async def pay_purchase(
    body: PayPurchaseBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        await assert_can_mutate_period(session, user, body.paid_date, uid)
        return await InventoryService(session).pay_purchase(
            purchase_id=body.purchase_id,
            amount=body.amount,
            paid_date=body.paid_date,
            debit_account_code=body.debit_account_code,
            credit_account_code=body.credit_account_code,
            unit_usaha_id=uid,
            created_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sales")
async def list_sales(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).list_sales(status=status, limit=limit)


@router.post("/sales/pay")
async def pay_sale(
    body: PaySaleBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
        await assert_can_mutate_period(session, user, body.paid_date, uid)
        return await InventoryService(session).pay_sale(
            sale_id=body.sale_id,
            amount=body.amount,
            paid_date=body.paid_date,
            debit_account_code=body.debit_account_code,
            credit_account_code=body.credit_account_code,
            unit_usaha_id=uid,
            created_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
