"""Unit-usaha isolation and recording-lock guards."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.identity.application.services import get_system_control
from modules.siabumdes.identity.infrastructure.models import ClosedPeriod, LockedPeriod, User
from modules.siabumdes.infrastructure.models import UnitUsaha
from shared.config import READONLY_ROLES, public_role

HQ_ROLES = {"admin", "direktur", "bendahara", "penasihat", "pengawas"}
WRITE_ROLES = {"admin", "direktur", "bendahara", "pengelola"}
TX_DELETE_ROLES = {"admin", "direktur", "bendahara"}
MASTER_WRITE_ROLES = {"admin"}
UNIT_WRITE_ROLES = {"admin"}


def role_of(user: User) -> str:
    return public_role(user.role)


def is_pengelola(user: User) -> bool:
    return role_of(user) == "pengelola"


def is_readonly(user: User) -> bool:
    return role_of(user) in READONLY_ROLES


def assert_not_readonly(user: User) -> None:
    if is_readonly(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akun bersifat read-only")


async def unit_code_for(session: AsyncSession, unit_usaha_id: Optional[str]) -> str:
    if not unit_usaha_id:
        return "BUMDES"
    unit = await session.get(UnitUsaha, unit_usaha_id)
    return unit.code if unit else "UNIT"


async def pengelola_unit(session: AsyncSession, user: User) -> UnitUsaha:
    if not user.unit_usaha_id:
        raise HTTPException(status_code=403, detail="Pengelola belum terikat unit usaha")
    unit = await session.get(UnitUsaha, user.unit_usaha_id)
    if not unit:
        raise HTTPException(status_code=403, detail="Unit usaha pengelola tidak ditemukan")
    return unit


async def scoped_unit_id(session: AsyncSession, user: User, requested: Optional[str]) -> Optional[str]:
    """Empty/None = BUMDES pusat. Pengelola always forced to own unit."""
    if is_pengelola(user):
        unit = await pengelola_unit(session, user)
        return unit.id
    return requested or None


def can_access_unit(user: User, unit_usaha_id: Optional[str]) -> bool:
    if role_of(user) in HQ_ROLES:
        return True
    if is_pengelola(user):
        return bool(user.unit_usaha_id) and unit_usaha_id == user.unit_usaha_id
    return False


async def assert_unit_active(session: AsyncSession, unit_usaha_id: Optional[str]) -> None:
    """Block NEW transactions/movements into a unit an admin has deactivated.

    unit_usaha_id=None means BUMDES pusat, which has no active/inactive
    concept -- always allowed. Existing rows already recorded against a
    unit that gets deactivated later are left untouched; this only stops
    new activity from being added.
    """
    if not unit_usaha_id:
        return
    unit = await session.get(UnitUsaha, unit_usaha_id)
    if unit and not unit.active:
        raise HTTPException(
            status_code=422,
            detail=f"Unit usaha {unit.code} sudah dinonaktifkan, tidak bisa mencatat transaksi baru",
        )


async def assert_can_mutate_period(
    session: AsyncSession,
    user: User,
    tx_date: date | str,
    unit_usaha_id: Optional[str],
) -> None:
    """Additional rules win: system lock and closed period block ALL roles."""
    if isinstance(tx_date, str):
        try:
            tx_date = date.fromisoformat(tx_date[:10])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Tanggal transaksi tidak valid") from exc

    control = await get_system_control(session)
    if control.recording_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pencatatan dikunci oleh Admin",
        )

    period = tx_date.strftime("%Y-%m")
    group_code = await unit_code_for(session, unit_usaha_id)

    # Urutan: tutup buku (berlaku untuk semua role, termasuk admin) diperiksa
    # lebih dulu supaya pesan errornya akurat, lalu kunci periode dan blokir
    # per pengguna (keduanya hanya untuk non-admin).
    if await _is_period_closed(session, period, group_code):
        raise HTTPException(
            status_code=400,
            detail=f"Buku periode {period} ({group_code}) sudah ditutup",
        )
    if role_of(user) != "admin":
        if await _is_period_locked(session, period, group_code):
            raise HTTPException(
                status_code=403,
                detail=f"Periode {period} ({group_code}) dikunci oleh Admin",
            )
        if period in (user.blocked_periods or []):
            raise HTTPException(status_code=403, detail=f"Periode {period} terkunci untuk akun ini")


async def _is_period_closed(session: AsyncSession, period: str, group_code: str) -> bool:
    row = await session.scalar(
        select(ClosedPeriod.id).where(
            ClosedPeriod.period == period,
            ClosedPeriod.group_code == group_code,
        )
    )
    return row is not None


async def _is_period_locked(session: AsyncSession, period: str, group_code: str) -> bool:
    row = await session.scalar(
        select(LockedPeriod.id).where(
            LockedPeriod.period == period,
            LockedPeriod.group_code.in_((group_code, "ALL")),
        )
    )
    return row is not None


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
