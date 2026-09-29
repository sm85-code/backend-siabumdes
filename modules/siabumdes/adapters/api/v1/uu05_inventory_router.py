"""UU05 inventory HTTP API (/api/v1/uu05_inventory)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.adapters.api.scope import assert_unit_active
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.infrastructure.models import UnitUsaha
from modules.siabumdes.inventory.application.services import (
    InventoryService,
    INVENTORY_BUSINESS_TYPES,
    UU05_CODE,
)
from shared.config import public_role
from shared.database import get_db

router = APIRouter(prefix="/api/v1/uu05_inventory", tags=["uu05_inventory"])

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
    vendor_id: str
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


@router.post("/stock-out-internal")
async def stock_out_internal(
    body: StockOutInternalBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    svc = InventoryService(session)
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
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


@router.post("/cancel-movement")
async def cancel_movement(
    body: CancelBody,
    _: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    await InventoryService(session).cancel_movement(body.stock_card_id)
    return {"cancelled": body.stock_card_id}


@router.get("/movements")
async def list_movements(
    product_id: Optional[str] = None,
    direction: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).list_movements(
        product_id=product_id, direction=direction, limit=limit
    )



@router.post("/cancel-adjustment")
async def cancel_adjustment(
    body: CancelAdjustBody,
    _: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    await InventoryService(session).cancel_adjustment(body.adjustment_id)
    return {"cancelled": body.adjustment_id}


@router.get("/adjustments")
async def list_adjustments(
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(require_inventory_user),
    session: AsyncSession = Depends(get_db),
):
    return await InventoryService(session).list_adjustments(limit=limit)


@router.post("/adjustments")
async def create_adjustment(
    body: AdjustBody,
    user: User = Depends(require_active_inventory_writer),
    session: AsyncSession = Depends(get_db),
):
    try:
        uid = await resolve_unit_id(session, body.unit_usaha_id, user)
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
