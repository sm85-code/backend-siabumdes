"""Normalized PostgreSQL ORM models for core finance.

Replaces legacy JSONB application_entities / Mongo-style documents.
All PKs are UUID strings (no ObjectId / _id).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AccountCategory(Base):
    __tablename__ = "account_categories"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    normal_balance: Mapped[str] = mapped_column(String(8), nullable=False)

    subcategories: Mapped[list["AccountSubcategory"]] = relationship(back_populates="category")


class AccountSubcategory(Base):
    __tablename__ = "account_subcategories"

    slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    category_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("account_categories.slug", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    category: Mapped["AccountCategory"] = relationship(back_populates="subcategories")


class UnitUsaha(Base):
    __tablename__ = "unit_usaha"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    revenue_scheme: Mapped[str] = mapped_column(Text, nullable=False, default="")
    business_type: Mapped[str] = mapped_column(String(20), nullable=False, default="jasa")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    accounts: Mapped[list["Account"]] = relationship(back_populates="unit_usaha")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="unit_usaha")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("code", "group_code", name="uq_accounts_code_group"),
        Index("ix_accounts_category", "category"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subcategory: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    normal_balance: Mapped[str] = mapped_column(String(8), nullable=False)
    parent_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    group_code: Mapped[str] = mapped_column(String(20), nullable=False, default="BUMDES", index=True)
    unit_usaha_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("unit_usaha.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    unit_usaha: Mapped[Optional["UnitUsaha"]] = relationship(back_populates="accounts")
    journal_items: Mapped[list["JournalItem"]] = relationship(back_populates="account")


class TransactionType(Base):
    __tablename__ = "transaction_types"
    __table_args__ = (UniqueConstraint("code", "group_code", name="uq_tx_types_code_group"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    debit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    credit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    group_code: Mapped[str] = mapped_column(String(20), nullable=False, default="BUMDES", index=True)
    unit_codes: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False, default=list)


class Mitra(Base):
    __tablename__ = "mitra"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_usaha_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("unit_usaha.id", ondelete="SET NULL"), nullable=True, index=True
    )
    phone: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Transaction(Base):
    """Business transaction header (source document)."""

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_date", "date"),
        Index("ix_transactions_reference", "reference"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    unit_usaha_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("unit_usaha.id", ondelete="SET NULL"), nullable=True, index=True
    )
    transaction_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    debit_account_code: Mapped[str] = mapped_column(String(64), nullable=False)
    credit_account_code: Mapped[str] = mapped_column(String(64), nullable=False)
    mitra_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reference: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    is_closing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    proofs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    unit_usaha: Mapped[Optional["UnitUsaha"]] = relationship(back_populates="transactions")
    journal_entry: Mapped[Optional["JournalEntry"]] = relationship(
        back_populates="transaction", uselist=False
    )


class JournalEntry(Base):
    """Double-entry journal header linked 1:1 to a Transaction."""

    __tablename__ = "journal_entries"
    __table_args__ = (Index("ix_journal_entries_date", "entry_date"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    transaction: Mapped["Transaction"] = relationship(back_populates="journal_entry")
    items: Mapped[list["JournalItem"]] = relationship(
        back_populates="journal_entry", cascade="all, delete-orphan"
    )


class JournalItem(Base):
    """Debit/credit line item — normalized from embedded JSONB legs."""

    __tablename__ = "journal_items"
    __table_args__ = (Index("ix_journal_items_account", "account_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    journal_entry_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)

    journal_entry: Mapped["JournalEntry"] = relationship(back_populates="items")
    account: Mapped["Account"] = relationship(back_populates="journal_items")


class YieldPartner(Base):
    """Standalone UU04 capital register; does not post financial journals."""
    __tablename__ = "yield_partners"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    unit_usaha_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("unit_usaha.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    capital: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class YieldPayment(Base):
    """Recorded monthly payout, independent of financial journal posting."""
    __tablename__ = "yield_payments"
    __table_args__ = (UniqueConstraint("partner_id", "year", "month", name="uq_yield_payment_period"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    partner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("yield_partners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(nullable=False)
    month: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    automatic: Mapped[bool] = mapped_column(Boolean, nullable=False)
