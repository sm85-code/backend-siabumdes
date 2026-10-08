"""UU04 partner capital register; monthly payments await a defined workflow."""
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.adapters.api.scope import assert_unit_active
from modules.siabumdes.identity.application.services import record_audit
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.infrastructure.models import UnitUsaha, YieldPartner
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
async def list_partners(unit: UnitUsaha = Depends(require_yield_unit), session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(YieldPartner).where(YieldPartner.unit_usaha_id == unit.id)
                                  .order_by(YieldPartner.created_at, YieldPartner.id))).scalars()
    return {"unit_active": unit.active, "items": [partner_out(row) for row in rows]}


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
