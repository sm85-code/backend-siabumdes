"""Monthly closing journals — POST /api/reports/close-period."""
from __future__ import annotations

from datetime import date

from modules.siabumdes.money_json import money_str
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import get_current_user, require_roles
from modules.siabumdes.adapters.api.scope import assert_can_mutate_period
from modules.siabumdes.identity.application.services import (
    list_closed_periods,
    list_locked_periods,
    lock_period,
    record_audit,
    unlock_period,
)
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.infrastructure.models import UnitUsaha
from modules.siabumdes.application.bagi_hasil_transfer import (
    list_bagi_hasil_transfers,
    run_bagi_hasil_transfer,
    transfer_transactions,
    undo_bagi_hasil_transfer,
)
from modules.siabumdes.application.closing import run_monthly_close, undo_monthly_close
from shared.database import get_db

router = APIRouter(prefix="/api", tags=["period-close"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class ClosePeriodRequest(BaseModel):
    period: str = Field(..., examples=["2026-09"])
    group: str = Field(default="BUMDES")


@router.post("/reports/close-period")
async def close_period(
    payload: ClosePeriodRequest,
    request: Request,
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    """Tutup buku bulanan: jurnal penutup + alokasi slug per entitas."""
    try:
        result = await run_monthly_close(
            session,
            period=payload.period,
            group=payload.group,
            actor_id=admin.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit(
        session, actor=admin, action="close_period", entity="closed_periods", entity_id=f"{payload.period}/{payload.group}",
        detail=f"Tutup periode {payload.period} ({payload.group})", ip=_client_ip(request),
    )
    return result


@router.get("/reports/closed-periods")
async def list_closed(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    rows = await list_closed_periods(session)
    return [
        {
            "period": row.period,
            "group": row.group_code,
            "laba_bersih": money_str(row.laba_bersih),
            "entries": row.entries,
            "closed_at": row.closed_at.isoformat(),
            "closed_by": row.closed_by,
        }
        for row in rows
    ]


@router.delete("/reports/close-period")
async def reopen_period(
    request: Request,
    period: str = Query(...),
    group: str = Query("BUMDES"),
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    try:
        deleted = await undo_monthly_close(session, period, group)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await record_audit(
        session, actor=admin, action="reopen_period", entity="closed_periods", entity_id=f"{period}/{group}",
        detail=f"Buka kembali periode {period} ({group}), {deleted} entri terhapus", ip=_client_ip(request),
    )
    return {"deleted_entries": deleted}


class LockPeriodRequest(BaseModel):
    period: str = Field(..., examples=["2026-09"])
    group: str = Field(default="ALL", description="ALL, BUMDES, atau kode unit")


@router.get("/reports/locked-periods")
async def list_locked(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    rows = await list_locked_periods(session)
    return [
        {
            "period": row.period,
            "group": row.group_code,
            "locked_at": row.locked_at.isoformat(),
            "locked_by": row.locked_by,
        }
        for row in rows
    ]


@router.post("/reports/lock-period")
async def lock_period_endpoint(
    payload: LockPeriodRequest,
    request: Request,
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    """Kunci periode untuk non-admin; admin masih boleh mengoreksi."""
    try:
        row = await lock_period(session, payload.period, payload.group, admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit(
        session, actor=admin, action="lock_period", entity="locked_periods", entity_id=f"{row.period}/{row.group_code}",
        detail=f"Kunci periode {row.period} ({row.group_code})", ip=_client_ip(request),
    )
    return {"locked": True, "period": row.period, "group": row.group_code}


@router.delete("/reports/lock-period")
async def unlock_period_endpoint(
    request: Request,
    period: str = Query(...),
    group: str = Query("ALL"),
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    try:
        await unlock_period(session, period, group)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await record_audit(
        session, actor=admin, action="unlock_period", entity="locked_periods", entity_id=f"{period}/{group}",
        detail=f"Buka kunci periode {period} ({group})", ip=_client_ip(request),
    )
    return {"locked": False}


class BagiHasilTransferRequest(BaseModel):
    period: str = Field(..., examples=["2026-03"])
    group: str = Field(default="BUMDES")


@router.post("/reports/bagi-hasil-transfer")
async def bagi_hasil_transfer(
    payload: BagiHasilTransferRequest,
    request: Request,
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    """Transfer bagi hasil: lunasi saldo utang bagi hasil setelah tutup buku."""
    try:
        result = await run_bagi_hasil_transfer(
            session, period=payload.period, group=payload.group, actor_id=admin.id
        )
        # Tanggal transaksi (1 bulan berikutnya) tidak boleh jatuh di periode yang sudah
        # ditutup; HTTPException di sini membatalkan seluruh transaksi database.
        when = date.fromisoformat(result["date"])
        await assert_can_mutate_period(session, admin, when, None)  # Pusat
        if result["group"] != "BUMDES":
            unit_id = await session.scalar(select(UnitUsaha.id).where(UnitUsaha.code == result["group"]))
            await assert_can_mutate_period(session, admin, when, unit_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit(
        session, actor=admin, action="bagi_hasil_transfer", entity="transactions",
        entity_id=f"{payload.period}/{result['group']}",
        detail=f"Transfer bagi hasil {payload.period} ({result['group']}) Rp {result['total']}", ip=_client_ip(request),
    )
    return result


@router.get("/reports/bagi-hasil-transfers")
async def list_bagi_hasil(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    return await list_bagi_hasil_transfers(session)


@router.delete("/reports/bagi-hasil-transfer")
async def cancel_bagi_hasil_transfer(
    request: Request,
    period: str = Query(...),
    group: str = Query("BUMDES"),
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    txs = await transfer_transactions(session, period, group)
    if not txs:
        raise HTTPException(status_code=404, detail="Transfer bagi hasil tidak ditemukan")
    for tx in txs:
        await assert_can_mutate_period(session, admin, tx.date, tx.unit_usaha_id)
    deleted = await undo_bagi_hasil_transfer(session, txs)
    await record_audit(
        session, actor=admin, action="bagi_hasil_cancel", entity="transactions", entity_id=f"{period}/{group}",
        detail=f"Batalkan transfer bagi hasil {period} ({group}), {deleted} transaksi terhapus", ip=_client_ip(request),
    )
    return {"deleted_transactions": deleted}
