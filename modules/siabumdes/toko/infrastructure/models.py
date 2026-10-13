"""Model Manajemen Toko (unit usaha UU05): data transaksi + isian mingguan."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.database import Base

TOKO_JENIS = ("pemasukan", "pengeluaran")


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TokoItem(Base):
    """Baris tab Data: Nama Transaksi + Jenis + Keterangan."""

    __tablename__ = "toko_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    jenis: Mapped[str] = mapped_column(String(16), nullable=False, default="pemasukan")
    keterangan: Mapped[str] = mapped_column(Text, nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Tanpa cascade: item yang sudah punya isian tidak boleh dihapus (lihat toko_router).
    entries: Mapped[list["TokoEntry"]] = relationship(back_populates="item", passive_deletes="all")


class TokoEntry(Base):
    """Isian satu sel Minggu ke-N untuk satu item pada satu periode (YYYY-MM)."""

    __tablename__ = "toko_entries"
    __table_args__ = (UniqueConstraint("item_id", "period", "week", name="uq_toko_entries_item_period_week"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("toko_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    tanggal: Mapped[date] = mapped_column(Date, nullable=False)
    nominal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    # Transaksi UU05 otomatis (reference TOKO:<entry_id>); lihat application/toko_transactions.py
    transaction_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    item: Mapped[TokoItem] = relationship(back_populates="entries")
