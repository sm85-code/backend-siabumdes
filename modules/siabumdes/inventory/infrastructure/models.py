"""Relational inventory models (UU05)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StockCategory(Base):
    __tablename__ = "stock_categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_usaha_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("unit_usaha.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("sku", "unit_usaha_id", name="uq_products_sku_unit"),
        Index("ix_products_unit", "unit_usaha_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stock_categories.id", ondelete="RESTRICT"), nullable=False
    )
    unit_usaha_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("unit_usaha.id", ondelete="RESTRICT"), nullable=False
    )
    unit_of_measure: Mapped[str] = mapped_column(String(20), nullable=False, default="pcs")
    cost_price: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    sell_price: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    qty_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    category: Mapped["StockCategory"] = relationship(back_populates="products")
    stock_cards: Mapped[list["StockCard"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    adjustments: Mapped[list["StockAdjustment"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class StockCard(Base):
    """Immutable movement ledger line (in/out)."""

    __tablename__ = "stock_cards"
    __table_args__ = (
        Index("ix_stock_cards_product_date", "product_id", "movement_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movement_date: Mapped[date] = mapped_column(Date, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # in | out
    movement_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="sale")
    # sale (default, stock-out ke pihak luar) | internal_use (pemakaian/transfer internal)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    total_value: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    reference: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    finance_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending | posted | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    product: Mapped["Product"] = relationship(back_populates="stock_cards")


class StockAdjustment(Base):
    """Manual / damage / expiry adjustments."""

    __tablename__ = "stock_adjustments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    adjustment_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)  # +/- qty
    reason: Mapped[str] = mapped_column(String(64), nullable=False)  # rusak|kadaluarsa|koreksi
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    product: Mapped["Product"] = relationship(back_populates="adjustments")


class Vendor(Base):
    """Supplier master data used by Stock In / Purchase transactions."""

    __tablename__ = "inventory_vendors"
    __table_args__ = (Index("ix_inventory_vendors_unit", "unit_usaha_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    unit_usaha_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("unit_usaha.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    purchases: Mapped[list["Purchase"]] = relationship(back_populates="vendor")


class Customer(Base):
    """Buyer master data used by Stock Out / Sales transactions."""

    __tablename__ = "inventory_customers"
    __table_args__ = (Index("ix_inventory_customers_unit", "unit_usaha_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    unit_usaha_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("unit_usaha.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    sales: Mapped[list["Sale"]] = relationship(back_populates="customer")


class Purchase(Base):
    """Purchase transaction generated by a Stock In movement."""

    __tablename__ = "inventory_purchases"
    __table_args__ = (
        UniqueConstraint("stock_card_id", name="uq_inventory_purchases_stock_card"),
        Index("ix_inventory_purchases_vendor", "vendor_id"),
        Index("ix_inventory_purchases_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    stock_card_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stock_cards.id", ondelete="CASCADE"), nullable=False
    )
    vendor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inventory_vendors.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)  # cash | credit
    total_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unpaid")  # unpaid|partial|paid
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    vendor: Mapped["Vendor"] = relationship(back_populates="purchases")
    payments: Mapped[list["PurchasePayment"]] = relationship(
        back_populates="purchase", cascade="all, delete-orphan"
    )


class PurchasePayment(Base):
    """Repayment history against a Purchase (utang usaha)."""

    __tablename__ = "inventory_purchase_payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    purchase_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inventory_purchases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    paid_date: Mapped[date] = mapped_column(Date, nullable=False)
    account_code: Mapped[str] = mapped_column(String(32), nullable=False)  # kas/bank account credited
    reference: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    purchase: Mapped["Purchase"] = relationship(back_populates="payments")


class Sale(Base):
    """Sales transaction generated by a Stock Out movement."""

    __tablename__ = "inventory_sales"
    __table_args__ = (
        UniqueConstraint("stock_card_id", name="uq_inventory_sales_stock_card"),
        Index("ix_inventory_sales_customer", "customer_id"),
        Index("ix_inventory_sales_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    stock_card_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stock_cards.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inventory_customers.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    sell_price: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)  # cash | piutang
    total_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unpaid")  # unpaid|partial|paid
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    customer: Mapped["Customer"] = relationship(back_populates="sales")
    payments: Mapped[list["SalePayment"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan"
    )


class SalePayment(Base):
    """Repayment history against a Sale (piutang usaha)."""

    __tablename__ = "inventory_sale_payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sale_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inventory_sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    paid_date: Mapped[date] = mapped_column(Date, nullable=False)
    account_code: Mapped[str] = mapped_column(String(32), nullable=False)  # kas/bank account debited
    reference: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    sale: Mapped["Sale"] = relationship(back_populates="payments")


class CommercialDocument(Base):
    """Issued immutable document; issuing never posts a stock/finance movement."""
    __tablename__ = 'inventory_documents'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='issued')
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_usaha_id: Mapped[str] = mapped_column(ForeignKey('unit_usaha.id', ondelete='RESTRICT'), index=True)
    partner_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    issued_date: Mapped[date] = mapped_column(Date, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentSource(Base):
    """One sale/purchase can belong to only one issued document of its kind."""
    __tablename__ = 'inventory_document_sources'
    __table_args__ = (UniqueConstraint('kind', 'source_id', name='uq_inventory_document_source'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey('inventory_documents.id', ondelete='RESTRICT'), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
