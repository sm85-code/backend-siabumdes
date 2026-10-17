"""Manajemen Toko HTTP API (/api/v1/toko).

Akses: admin, direktur, bendahara, dan pengelola yang terikat ke unit usaha
UU05 (unit 5 / toko). Pengelola unit lain ditolak 403.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.adapters.api.scope import assert_can_mutate_period, assert_unit_active
from modules.siabumdes.application.toko_transactions import (
    apply_entry_transaction,
    delete_entry_transaction,
    toko_unit,
)
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.infrastructure.models import Transaction, UnitUsaha
from modules.siabumdes.toko.infrastructure.models import TokoEntry, TokoItem
from shared.config import public_role
from shared.database import get_db

router = APIRouter(prefix="/api/v1/toko", tags=["toko"])

TOKO_ROLES = ("admin", "direktur", "bendahara", "pengelola")
TOKO_UNIT_CODE = "UU05"
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


async def require_toko_user(
    user: User = Depends(require_roles(*TOKO_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> User:
    if public_role(user.role) != "pengelola":
        return user
    if not user.unit_usaha_id:
        raise HTTPException(status_code=403, detail="Pengelola belum terikat unit usaha")
    unit = await session.get(UnitUsaha, user.unit_usaha_id)
    if not unit or (unit.code or "").upper() != TOKO_UNIT_CODE:
        raise HTTPException(status_code=403, detail="Manajemen Toko hanya untuk pengelola unit 5 (UU05)")
    return user


async def require_toko_writer(
    user: User = Depends(require_toko_user),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Sama seperti Imbal Hasil: unit UU05 yang nonaktif hanya bisa dilihat."""
    unit = await toko_unit(session)
    await assert_unit_active(session, unit.id)
    return user


def _check_period(period: str) -> str:
    if not PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="Periode harus berformat YYYY-MM")
    return period


def week5_has_saturday(period: str) -> bool:
    """Minggu ke-1 = tgl 1 s.d. Sabtu pertama; True bila Sabtu minggu ke-5 masih di bulan ini."""
    y, m = (int(x) for x in period.split("-"))
    last = calendar.monthrange(y, m)[1]
    first_saturday = 1 + (5 - date(y, m, 1).weekday()) % 7  # weekday(): Senin=0, Sabtu=5
    return first_saturday + 28 <= last


def item_out(item: TokoItem, has_entries: bool = False) -> dict:
    return {
        "has_entries": has_entries,
        "id": item.id,
        "name": item.name,
        "jenis": item.jenis,
        "keterangan": item.keterangan or "",
        "position": item.position,
    }


def entry_out(entry: TokoEntry) -> dict:
    return {
        "id": entry.id,
        "item_id": entry.item_id,
        "period": entry.period,
        "week": entry.week,
        "tanggal": entry.tanggal.isoformat(),
        "nominal": str(entry.nominal),
        "transaction_id": entry.transaction_id,
    }


class ItemCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    jenis: Literal["pemasukan", "pengeluaran"]
    keterangan: str = ""
    position: Optional[int] = None


class ItemUpdateBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    jenis: Optional[Literal["pemasukan", "pengeluaran"]] = None
    keterangan: Optional[str] = None
    position: Optional[int] = None


class EntryUpsertBody(BaseModel):
    item_id: str
    period: str
    week: int = Field(ge=1, le=5)
    tanggal: date
    nominal: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


@router.get("/items")
async def list_items(
    _: User = Depends(require_toko_user),
    session: AsyncSession = Depends(get_db),
):
    rows = list((await session.execute(select(TokoItem).order_by(TokoItem.position, TokoItem.created_at))).scalars())
    used = set((await session.execute(select(TokoEntry.item_id).distinct())).scalars()) if rows else set()
    return [item_out(r, r.id in used) for r in rows]


@router.post("/items")
async def create_item(
    body: ItemCreateBody,
    user: User = Depends(require_toko_writer),
    session: AsyncSession = Depends(get_db),
):
    position = body.position
    if position is None:
        position = int(await session.scalar(select(func.coalesce(func.max(TokoItem.position), 0))) or 0) + 1
    item = TokoItem(
        name=body.name.strip(),
        jenis=body.jenis,
        keterangan=body.keterangan,
        position=position,
        created_by=user.username,
    )
    session.add(item)
    await session.flush()
    return item_out(item)


@router.put("/items/{item_id}")
async def update_item(
    item_id: str,
    body: ItemUpdateBody,
    user: User = Depends(require_toko_writer),
    session: AsyncSession = Depends(get_db),
):
    item = await session.get(TokoItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Data transaksi tidak ditemukan")
    old_name, old_jenis = item.name, item.jenis
    if body.name is not None:
        item.name = body.name.strip()
    if body.jenis is not None:
        item.jenis = body.jenis
    if body.keterangan is not None:
        item.keterangan = body.keterangan
    if body.position is not None:
        item.position = body.position
    jenis_changed = body.jenis is not None and body.jenis != old_jenis
    if (body.name is not None and item.name != old_name) or jenis_changed:
        # Nama/jenis berubah -> keterangan & jenis transaksi UU05 terkait ikut diperbarui.
        # Ganti jenis mengubah akun jurnal: ditolak bila ada isian di periode tutup buku.
        # Ganti nama saja: isian di periode tutup buku/dikunci dilewati (sama seperti ganti nama mitra).
        entries = (await session.execute(select(TokoEntry).where(TokoEntry.item_id == item.id))).scalars().all()
        if entries:
            unit = await toko_unit(session)
            for entry in entries:
                if not jenis_changed:
                    try:
                        await assert_can_mutate_period(session, user, entry.tanggal, unit.id)
                        if entry.transaction_id:
                            tx = await session.get(Transaction, entry.transaction_id)
                            if tx:
                                await assert_can_mutate_period(session, user, tx.date, unit.id)
                    except HTTPException:
                        continue
                await apply_entry_transaction(session, user, unit, entry, item)
    await session.flush()
    has = bool(await session.scalar(select(func.count(TokoEntry.id)).where(TokoEntry.item_id == item.id)))
    return item_out(item, has)


@router.delete("/items/{item_id}")
async def delete_item(
    item_id: str,
    _: User = Depends(require_toko_writer),
    session: AsyncSession = Depends(get_db),
):
    item = await session.get(TokoItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Data transaksi tidak ditemukan")
    used = await session.scalar(select(func.count(TokoEntry.id)).where(TokoEntry.item_id == item_id))
    if used:
        raise HTTPException(
            status_code=409,
            detail="Data transaksi tidak dapat dihapus karena sudah memiliki nominal di tab Transaksi. "
            "Hapus seluruh isian nominalnya terlebih dahulu.",
        )
    await session.delete(item)
    await session.flush()
    return {"deleted": item_id}


@router.get("/entries")
async def list_entries(
    period: str = Query(...),
    _: User = Depends(require_toko_user),
    session: AsyncSession = Depends(get_db),
):
    _check_period(period)
    rows = (await session.execute(select(TokoEntry).where(TokoEntry.period == period))).scalars()
    return [entry_out(r) for r in rows]


@router.put("/entries")
async def upsert_entry(
    body: EntryUpsertBody,
    user: User = Depends(require_toko_writer),
    session: AsyncSession = Depends(get_db),
):
    _check_period(body.period)
    if body.tanggal.strftime("%Y-%m") != body.period:
        raise HTTPException(
            status_code=422,
            detail="Tanggal transaksi harus berada dalam periode bulan yang dipilih.",
        )
    item = await session.get(TokoItem, body.item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Data transaksi tidak ditemukan")
    entry = await session.scalar(
        select(TokoEntry).where(
            TokoEntry.item_id == body.item_id,
            TokoEntry.period == body.period,
            TokoEntry.week == body.week,
        )
    )
    if entry is None:
        entry = TokoEntry(item_id=body.item_id, period=body.period, week=body.week, created_by=user.username)
        session.add(entry)
    entry.tanggal = body.tanggal
    entry.nominal = body.nominal
    await session.flush()
    await apply_entry_transaction(session, user, await toko_unit(session), entry, item)
    await session.flush()
    return entry_out(entry)


@router.delete("/entries/{entry_id}")
async def delete_entry(
    entry_id: str,
    user: User = Depends(require_toko_writer),
    session: AsyncSession = Depends(get_db),
):
    entry = await session.get(TokoEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Isian tidak ditemukan")
    await delete_entry_transaction(session, user, await toko_unit(session), entry)
    await session.delete(entry)
    await session.flush()
    return {"deleted": entry_id}


@router.get("/neraca")
async def neraca(
    dates: list[date] = Query(..., max_length=12),
    _: User = Depends(require_toko_user),
    session: AsyncSession = Depends(get_db),
):
    """Posisi neraca kumulatif per tanggal (lihat toko.application.neraca)."""
    from modules.siabumdes.toko.application.neraca import MODAL, OPENING_DATE, kas_breakdown, load, position_with_opening

    data = await load(session)
    return {
        "modal": str(MODAL),
        "opening_date": OPENING_DATE.isoformat(),
        "positions": [{
            "date": d.isoformat(),
            **{k: str(v) for k, v in position_with_opening(data, d).items()},
            "kas_detail": {k: str(v) for k, v in kas_breakdown(data, d).items()},
        } for d in dates],
    }


@router.get("/journal")
async def journal(
    period: str = Query(...),
    _: User = Depends(require_toko_user),
    session: AsyncSession = Depends(get_db),
):
    """Transaksi manual menu Transaksi unit UU05 pada periode (dibaca Perduitan #1/#2/#3)."""
    from modules.siabumdes.toko.application.journal import load_journal

    _check_period(period)
    y, m = map(int, period.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return [r.out() for r in await load_journal(session, start, end)]
