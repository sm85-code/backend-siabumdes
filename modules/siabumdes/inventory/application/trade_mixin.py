"""Vendor/Customer master data and Purchase/Sale (utang-piutang) tracking."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from modules.siabumdes.inventory.application.coa import validate_coa_codes
from modules.siabumdes.inventory.infrastructure.models import (
    Customer,
    Purchase,
    PurchasePayment,
    Sale,
    SalePayment,
    Vendor,
)

_PAYMENT_TOLERANCE = Decimal("0.01")


def _partner_dict(row: Vendor | Customer) -> dict[str, Any]:
    return {
        "id": row.id,
        "unit_usaha_id": row.unit_usaha_id,
        "name": row.name,
        "contact": row.contact,
        "address": row.address,
        "is_active": row.is_active,
    }


def _purchase_dict(p: Purchase) -> dict[str, Any]:
    paid = sum((pay.amount for pay in p.payments), Decimal("0.00"))
    return {
        "id": p.id,
        "stock_card_id": p.stock_card_id,
        "vendor_id": p.vendor_id,
        "vendor_name": p.vendor.name if p.vendor else None,
        "invoice_number": p.invoice_number,
        "payment_method": p.payment_method,
        "total_amount": str(p.total_amount),
        "paid_amount": str(paid),
        "outstanding": str((p.total_amount - paid).quantize(Decimal("0.01"))),
        "due_date": p.due_date.isoformat() if p.due_date else None,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _sale_dict(s: Sale) -> dict[str, Any]:
    paid = sum((pay.amount for pay in s.payments), Decimal("0.00"))
    return {
        "id": s.id,
        "stock_card_id": s.stock_card_id,
        "customer_id": s.customer_id,
        "customer_name": s.customer.name if s.customer else None,
        "invoice_number": s.invoice_number,
        "sell_price": str(s.sell_price),
        "payment_method": s.payment_method,
        "total_amount": str(s.total_amount),
        "paid_amount": str(paid),
        "outstanding": str((s.total_amount - paid).quantize(Decimal("0.01"))),
        "due_date": s.due_date.isoformat() if s.due_date else None,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


class InventoryTradeMixin:
    # ---- Vendors ----
    async def list_vendors(self, *, unit_usaha_id: Optional[str] = None) -> list[dict[str, Any]]:
        stmt = select(Vendor).order_by(Vendor.name.asc())
        if unit_usaha_id:
            stmt = stmt.where(Vendor.unit_usaha_id == unit_usaha_id)
        rows = (await self.session.scalars(stmt)).all()
        return [_partner_dict(r) for r in rows]

    async def create_vendor(self, *, unit_usaha_id: str, name: str, contact: Optional[str], address: Optional[str]) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("nama vendor wajib diisi")
        row = Vendor(unit_usaha_id=unit_usaha_id, name=name.strip(), contact=contact, address=address)
        self.session.add(row)
        await self.session.flush()
        return _partner_dict(row)

    async def update_vendor(
        self, vendor_id: str, *, name: Optional[str] = None, contact: Optional[str] = None,
        address: Optional[str] = None, is_active: Optional[bool] = None,
    ) -> dict[str, Any]:
        row = await self.session.get(Vendor, vendor_id)
        if not row:
            raise ValueError("vendor tidak ditemukan")
        if name is not None:
            row.name = name.strip() or row.name
        if contact is not None:
            row.contact = contact
        if address is not None:
            row.address = address
        if is_active is not None:
            row.is_active = is_active
        await self.session.flush()
        return _partner_dict(row)

    # ---- Customers ----
    async def list_customers(self, *, unit_usaha_id: Optional[str] = None) -> list[dict[str, Any]]:
        stmt = select(Customer).order_by(Customer.name.asc())
        if unit_usaha_id:
            stmt = stmt.where(Customer.unit_usaha_id == unit_usaha_id)
        rows = (await self.session.scalars(stmt)).all()
        return [_partner_dict(r) for r in rows]

    async def create_customer(self, *, unit_usaha_id: str, name: str, contact: Optional[str], address: Optional[str]) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("nama customer wajib diisi")
        row = Customer(unit_usaha_id=unit_usaha_id, name=name.strip(), contact=contact, address=address)
        self.session.add(row)
        await self.session.flush()
        return _partner_dict(row)

    async def update_customer(
        self, customer_id: str, *, name: Optional[str] = None, contact: Optional[str] = None,
        address: Optional[str] = None, is_active: Optional[bool] = None,
    ) -> dict[str, Any]:
        row = await self.session.get(Customer, customer_id)
        if not row:
            raise ValueError("customer tidak ditemukan")
        if name is not None:
            row.name = name.strip() or row.name
        if contact is not None:
            row.contact = contact
        if address is not None:
            row.address = address
        if is_active is not None:
            row.is_active = is_active
        await self.session.flush()
        return _partner_dict(row)

    # ---- Purchases (utang) ----
    async def list_purchases(self, *, status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        stmt = (
            select(Purchase)
            .options(selectinload(Purchase.vendor), selectinload(Purchase.payments))
            .order_by(Purchase.created_at.desc())
            .limit(min(limit, 500))
        )
        if status:
            stmt = stmt.where(Purchase.status == status)
        rows = (await self.session.scalars(stmt)).all()
        return [_purchase_dict(p) for p in rows]

    async def pay_purchase(
        self, *, purchase_id: str, amount: Decimal, paid_date: date,
        debit_account_code: str, credit_account_code: str, unit_usaha_id: str,
        created_by: str = "system-inventory",
    ) -> dict[str, Any]:
        purchase = await self.session.scalar(
            select(Purchase)
            .options(selectinload(Purchase.vendor), selectinload(Purchase.payments))
            .where(Purchase.id == purchase_id)
        )
        if not purchase:
            raise ValueError("transaksi pembelian tidak ditemukan")
        if purchase.payment_method != "credit":
            raise ValueError("pembelian tunai tidak memerlukan pelunasan")
        if amount <= 0:
            raise ValueError("jumlah pelunasan harus > 0")
        paid_so_far = sum((p.amount for p in purchase.payments), Decimal("0.00"))
        outstanding = purchase.total_amount - paid_so_far
        if amount - outstanding > _PAYMENT_TOLERANCE:
            raise ValueError("jumlah pelunasan melebihi sisa utang")
        await validate_coa_codes(self.session, debit_account_code, credit_account_code)
        payment = PurchasePayment(
            purchase_id=purchase.id, amount=amount, paid_date=paid_date,
            account_code=credit_account_code, reference="",
        )
        self.session.add(payment)
        await self.session.flush()
        payment.reference = f"purchase-pay:{payment.id}"
        await self.finance.record_inventory_journal(
            movement_date=paid_date,
            unit_usaha_id=unit_usaha_id,
            amount=amount,
            debit_account_code=debit_account_code,
            credit_account_code=credit_account_code,
            description=f"Pelunasan utang usaha - invoice {purchase.invoice_number}",
            reference=payment.reference,
            created_by=created_by,
            transaction_type="purchase_payment",
        )
        new_paid = paid_so_far + amount
        if purchase.total_amount - new_paid <= _PAYMENT_TOLERANCE:
            purchase.status = "paid"
        else:
            purchase.status = "partial"
        await self.session.flush()
        await self.session.refresh(purchase, attribute_names=["payments"])
        return _purchase_dict(purchase)

    # ---- Sales (piutang) ----
    async def list_sales(self, *, status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        stmt = (
            select(Sale)
            .options(selectinload(Sale.customer), selectinload(Sale.payments))
            .order_by(Sale.created_at.desc())
            .limit(min(limit, 500))
        )
        if status:
            stmt = stmt.where(Sale.status == status)
        rows = (await self.session.scalars(stmt)).all()
        return [_sale_dict(s) for s in rows]

    async def pay_sale(
        self, *, sale_id: str, amount: Decimal, paid_date: date,
        debit_account_code: str, credit_account_code: str, unit_usaha_id: str,
        created_by: str = "system-inventory",
    ) -> dict[str, Any]:
        sale = await self.session.scalar(
            select(Sale)
            .options(selectinload(Sale.customer), selectinload(Sale.payments))
            .where(Sale.id == sale_id)
        )
        if not sale:
            raise ValueError("transaksi penjualan tidak ditemukan")
        if sale.payment_method != "piutang":
            raise ValueError("penjualan tunai tidak memerlukan pelunasan")
        if amount <= 0:
            raise ValueError("jumlah pelunasan harus > 0")
        paid_so_far = sum((p.amount for p in sale.payments), Decimal("0.00"))
        outstanding = sale.total_amount - paid_so_far
        if amount - outstanding > _PAYMENT_TOLERANCE:
            raise ValueError("jumlah pelunasan melebihi sisa piutang")
        await validate_coa_codes(self.session, debit_account_code, credit_account_code)
        payment = SalePayment(
            sale_id=sale.id, amount=amount, paid_date=paid_date,
            account_code=debit_account_code, reference="",
        )
        self.session.add(payment)
        await self.session.flush()
        payment.reference = f"sale-pay:{payment.id}"
        await self.finance.record_inventory_journal(
            movement_date=paid_date,
            unit_usaha_id=unit_usaha_id,
            amount=amount,
            debit_account_code=debit_account_code,
            credit_account_code=credit_account_code,
            description=f"Pelunasan piutang usaha - invoice {sale.invoice_number}",
            reference=payment.reference,
            created_by=created_by,
            transaction_type="sale_payment",
        )
        new_paid = paid_so_far + amount
        if sale.total_amount - new_paid <= _PAYMENT_TOLERANCE:
            sale.status = "paid"
        else:
            sale.status = "partial"
        await self.session.flush()
        await self.session.refresh(sale, attribute_names=["payments"])
        return _sale_dict(sale)
