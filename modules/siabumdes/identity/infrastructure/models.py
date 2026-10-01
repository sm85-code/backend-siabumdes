"""Users, system-wide recording lock, and closed accounting periods."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"), UniqueConstraint("email", name="uq_users_email"))

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # bcrypt/passlib hashes are ~60 chars; never use String(32)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    unit_usaha_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    blocked_periods: Mapped[list[str]] = mapped_column(ARRAY(String(7)), nullable=False, default=list)
    photo_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SystemControl(Base):
    """Singleton row (id=default) for application-wide recording lock."""

    __tablename__ = "system_controls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    recording_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OrgProfile(Base):
    """Singleton row (id=default): kop surat/letterhead untuk export PDF/Excel/Word,
    diedit lewat menu Profil BUMDES (bukan lagi env var statis)."""

    __tablename__ = "org_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    org_name: Mapped[str] = mapped_column(String(255), nullable=False, default="BUMDes")
    org_legal_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Badan Usaha Milik Desa")
    address: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    village: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    district: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    regency: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    province: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tagline: Mapped[str] = mapped_column(String(255), nullable=False, default="Laporan Keuangan")
    logo_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    signatory_left_title: Mapped[str] = mapped_column(String(120), nullable=False, default="Direktur / Ketua")
    signatory_left_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    signatory_mid_title: Mapped[str] = mapped_column(String(120), nullable=False, default="Bendahara")
    signatory_mid_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    signatory_right_title: Mapped[str] = mapped_column(String(120), nullable=False, default="Mengetahui")
    signatory_right_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    primary_color: Mapped[str] = mapped_column(String(6), nullable=False, default="1F4E79")

    # Proporsi bagi hasil (persen, 0-100). Dipakai saat tutup buku bulanan
    # (modules.siabumdes.application.bagi_hasil / closing.py) dan di laporan
    # (perubahan_ekuitas/per_unit di reporting.py) -- diedit lewat menu Profil
    # BUMDES, bukan lagi konstanta hardcode. Grup BUMDES (6 angka) wajib total
    # 100; grup unit usaha (2 angka) wajib total 100 -- divalidasi di router.
    share_pengurus: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("35"))
    share_penasihat: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("7"))
    share_pengawas: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("5"))
    share_dana_sosial: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("5"))
    share_pades: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("30"))
    share_modal_bumdes: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("18"))
    share_unit_pengelola: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("30"))
    share_unit_bumdes: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("70"))

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class AuditLog(Base):
    """Jejak aksi sensitif (kelola akun, kunci sistem, tutup periode, master
    data) -- bukan tiap transaksi harian: login, hapus/ubah akun, dan aksi
    admin-only lain yang berdampak besar/susah dibalikkan."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    actor_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False, default="-")
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False, default="-")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class LockedPeriod(Base):
    """Kunci periode: non-admin tidak boleh menulis, admin masih boleh.

    group_code: "ALL" (semua grup), "BUMDES", atau kode unit (UU01..).
    """

    __tablename__ = "locked_periods"
    __table_args__ = (UniqueConstraint("period", "group_code", name="uq_locked_periods_period_group"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # YYYY-MM
    group_code: Mapped[str] = mapped_column(String(32), nullable=False, default="ALL")
    locked_by: Mapped[str] = mapped_column(String(64), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ClosedPeriod(Base):
    __tablename__ = "closed_periods"
    __table_args__ = (UniqueConstraint("period", "group_code", name="uq_closed_periods_period_group"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # YYYY-MM
    group_code: Mapped[str] = mapped_column(String(32), nullable=False, default="BUMDES")
    laba_bersih: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
