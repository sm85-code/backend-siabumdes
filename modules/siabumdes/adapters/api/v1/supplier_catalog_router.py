"""Unit-scoped product/retail-price/stock sharing, with no inventory mutations."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.infrastructure.models import UnitUsaha
from modules.siabumdes.inventory.infrastructure.models import Product, StockCategory, SupplierCatalogAccess
from shared.config import public_role
from shared.database import get_db

router = APIRouter(prefix="/api/v1/uu05_inventory/supplier-catalog", tags=["supplier_catalog"])


class AccessInput(BaseModel):
    unit_id: str | None = Field(default=None, max_length=36)


@router.post("/access")
async def create_access(
    body: AccessInput, user: User = Depends(require_roles("admin")), session: AsyncSession = Depends(get_db),
):
    unit = await session.get(UnitUsaha, body.unit_id) if body.unit_id else await session.scalar(
        select(UnitUsaha).where(UnitUsaha.code == "UU05")
    )
    if not unit:
        raise HTTPException(422, "Unit 5 tidak ditemukan")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=365)
    session.add(SupplierCatalogAccess(token_digest=hashlib.sha256(token.encode()).hexdigest(),
                unit_usaha_id=unit.id, issued_by=user.id, expires_at=expires))
    await session.commit()
    return {"token": token, "unit_id": unit.id, "unit_name": unit.name, "expires_at": expires.isoformat()}


async def catalog_access(
    key: str = Header(default="", alias="X-ERP-Catalog-Key"), session: AsyncSession = Depends(get_db),
):
    if not key or len(key) > 200:
        raise HTTPException(401, "Koneksi katalog tidak valid")
    access = await session.scalar(select(SupplierCatalogAccess).where(
        SupplierCatalogAccess.token_digest == hashlib.sha256(key.encode()).hexdigest()
    ))
    if not access:
        raise HTTPException(401, "Koneksi katalog tidak valid")
    expires = access.expires_at.replace(tzinfo=timezone.utc) if access.expires_at.tzinfo is None else access.expires_at
    owner = await session.get(User, access.issued_by)
    if expires <= datetime.now(timezone.utc) or not owner or not owner.active or public_role(owner.role) != "admin":
        raise HTTPException(401, "Koneksi katalog berakhir; hubungkan ulang")
    return access


@router.get("")
async def read_catalog(access: SupplierCatalogAccess = Depends(catalog_access), session: AsyncSession = Depends(get_db)):
    unit = await session.get(UnitUsaha, access.unit_usaha_id)
    if not unit:
        raise HTTPException(404, "Unit katalog tidak ditemukan")
    rows = (await session.execute(
        select(Product, StockCategory.name).join(StockCategory, StockCategory.id == Product.category_id)
        .where(Product.unit_usaha_id == access.unit_usaha_id).order_by(Product.name, Product.sku)
    )).all()
    return {
        "unit_id": unit.id, "unit_name": unit.name, "read_at": datetime.now(timezone.utc).isoformat(),
        "items": [dict(id=p.id, sku=p.sku, name=p.name, category_name=category, unit_of_measure=p.unit_of_measure,
                       sell_price=str(p.sell_price), qty_on_hand=p.qty_on_hand) for p, category in rows],
    }


@router.delete("/access")
async def revoke_access(access: SupplierCatalogAccess = Depends(catalog_access), session: AsyncSession = Depends(get_db)):
    await session.delete(access)
    await session.commit()
    return {"disconnected": True}
