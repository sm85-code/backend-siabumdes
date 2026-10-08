"""UU04 partner register and annual monthly payout records (no journal posting)."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.adapters.api.scope import assert_unit_active
from modules.siabumdes.identity.application.services import record_audit
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.infrastructure.models import UnitUsaha, YieldPartner, YieldPayment
from shared.config import public_role
from shared.database import get_db

router = APIRouter(prefix="/api/imbal-hasil", tags=["imbal-hasil"])


async def require_yield_unit(
    user: User = Depends(require_roles("admin", "direktur", "bendahara", "pengelola")),
    session: AsyncSession = Depends(get_db),
) -> UnitUsaha:
    unit = (await session.execute(select(UnitUsaha).where(UnitUsaha.code == "UU04"))).scalar_one_or_none()
    if public_role(user.role) == "pengelola" and (not unit or user.unit_usaha_id != unit.id):
        raise HTTPException(403, "Imbal Hasil hanya untuk pengelola unit 4")
    if not unit:
        raise HTTPException(404, "Unit usaha UU04 belum tersedia")
    return unit


async def require_yield_writer(
    unit: UnitUsaha = Depends(require_yield_unit), session: AsyncSession = Depends(get_db),
) -> UnitUsaha:
    await assert_unit_active(session, unit.id)
    return unit


class PartnerIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    capital: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


def partner_out(row: YieldPartner) -> dict:
    return {"id": row.id, "name": row.name, "capital": str(row.capital),
            "yield_amount": str((row.capital * Decimal("0.03")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))}


async def partner_for(session: AsyncSession, unit: UnitUsaha, partner_id: str) -> YieldPartner:
    row = await session.get(YieldPartner, partner_id)
    if not row or row.unit_usaha_id != unit.id:
        raise HTTPException(404, "Mitra tidak ditemukan")
    return row


@router.get("/mitra")
async def list_partners(year: int | None = Query(None, ge=1900, le=9999), unit: UnitUsaha = Depends(require_yield_unit), session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(YieldPartner).where(YieldPartner.unit_usaha_id == unit.id)
                                  .order_by(YieldPartner.created_at, YieldPartner.id))).scalars()
    year = year or date.today().year
    items = [partner_out(row) for row in rows]
    payments = (await session.execute(select(YieldPayment).join(YieldPartner)
        .where(YieldPartner.unit_usaha_id == unit.id, YieldPayment.year == year))).scalars()
    by_partner = {row["id"]: row for row in items}
    for row in items:
        row["payments"] = {}
    for payment in payments:
        if payment.partner_id in by_partner:
            by_partner[payment.partner_id]["payments"][str(payment.month)] = str(payment.amount)
    return {"unit_active": unit.active, "items": items}


async def save_partner(body, row, session, actor, request, action):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Nama mitra wajib diisi")
    row.name, row.capital = name, body.capital
    session.add(row)
    await session.flush()
    await record_audit(session, actor=actor, action=action, entity="yield_partners", entity_id=row.id,
                       detail=f"{action}: {row.name}", ip=request.client.host if request.client else "")
    return partner_out(row)


@router.post("/mitra", status_code=201)
async def create_partner(body: PartnerIn, request: Request, unit: UnitUsaha = Depends(require_yield_writer),
                         session: AsyncSession = Depends(get_db),
                         actor: User = Depends(require_roles("admin", "direktur", "bendahara", "pengelola"))):
    return await save_partner(body, YieldPartner(unit_usaha_id=unit.id), session, actor, request, "create_yield_partner")


@router.put("/mitra/{partner_id}")
async def update_partner(partner_id: str, body: PartnerIn, request: Request,
                         unit: UnitUsaha = Depends(require_yield_writer), session: AsyncSession = Depends(get_db),
                         actor: User = Depends(require_roles("admin", "direktur", "bendahara", "pengelola"))):
    row = await partner_for(session, unit, partner_id)
    return await save_partner(body, row, session, actor, request, "update_yield_partner")


@router.delete("/mitra/{partner_id}", status_code=204)
async def delete_partner(partner_id: str, request: Request, unit: UnitUsaha = Depends(require_yield_writer),
                         session: AsyncSession = Depends(get_db),
                         actor: User = Depends(require_roles("admin", "direktur", "bendahara", "pengelola"))):
    row = await partner_for(session, unit, partner_id)
    await record_audit(session, actor=actor, action="delete_yield_partner", entity="yield_partners", entity_id=row.id,
                       detail=f"Hapus mitra {row.name}", ip=request.client.host if request.client else "")
    await session.delete(row)


class PaymentIn(BaseModel):
    year: int = Field(ge=1900, le=9999)
    month: int = Field(ge=1, le=12)
    automatic: bool = False
    amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)


def payment_amount(body: PaymentIn, partner: YieldPartner) -> Decimal:
    if body.automatic:
        return (partner.capital * Decimal("0.03")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if body.amount is None:
        raise HTTPException(422, "Pilih nilai otomatis atau isi nominal manual, dan pilih bulan")
    return body.amount


@router.put("/mitra/{partner_id}/pembayaran")
async def save_payment(partner_id: str, body: PaymentIn, request: Request,
                       unit: UnitUsaha = Depends(require_yield_writer), session: AsyncSession = Depends(get_db),
                       actor: User = Depends(require_roles("admin", "direktur", "bendahara", "pengelola"))):
    partner = await partner_for(session, unit, partner_id)
    amount = payment_amount(body, partner)
    stmt = insert(YieldPayment).values(partner_id=partner.id, year=body.year, month=body.month,
                                       amount=amount, automatic=body.automatic)
    await session.execute(stmt.on_conflict_do_update(constraint="uq_yield_payment_period",
        set_={"amount": amount, "automatic": body.automatic}))
    await record_audit(session, actor=actor, action="save_yield_payment", entity="yield_partners", entity_id=partner.id,
        detail=f"Pembayaran {body.year}-{body.month:02d}: {amount}", ip=request.client.host if request.client else "")
    return {"month": body.month, "year": body.year, "amount": str(amount)}


@router.delete("/mitra/{partner_id}/pembayaran", status_code=204)
async def delete_payment(partner_id: str, body: PaymentIn, request: Request,
                         unit: UnitUsaha = Depends(require_yield_writer), session: AsyncSession = Depends(get_db),
                         actor: User = Depends(require_roles("admin", "direktur", "bendahara", "pengelola"))):
    partner = await partner_for(session, unit, partner_id)
    payment_amount(body, partner)  # same input requirements as save
    result = await session.execute(delete(YieldPayment).where(YieldPayment.partner_id == partner.id,
                                    YieldPayment.year == body.year, YieldPayment.month == body.month))
    if not result.rowcount:
        raise HTTPException(404, "Belum ada pembayaran untuk bulan yang dipilih")
    await record_audit(session, actor=actor, action="delete_yield_payment", entity="yield_partners", entity_id=partner.id,
        detail=f"Hapus pembayaran {body.year}-{body.month:02d}", ip=request.client.host if request.client else "")
