"""Profil BUMDES: kop surat (letterhead) untuk export PDF/Excel/Word, editable
lewat UI alih-alih env var statis. Baca boleh siapa saja yang login (dipakai
di halaman Laporan); ubah/upload logo khusus admin."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import get_current_user, require_roles
from modules.siabumdes.adapters.external.org_logo_upload import upload_org_logo
from modules.siabumdes.application.bagi_hasil import (
    SHARE_FIELDS_BUMDES,
    SHARE_FIELDS_UNIT,
    validate_bagi_hasil_fields,
)
from modules.siabumdes.identity.application.services import get_org_profile, record_audit, update_org_profile
from modules.siabumdes.identity.infrastructure.models import OrgProfile
from shared.database import get_db

router = APIRouter(prefix="/api/org-profile", tags=["org-profile"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _out(row: OrgProfile) -> dict:
    return {
        "org_name": row.org_name,
        "org_legal_name": row.org_legal_name,
        "address": row.address,
        "village": row.village,
        "district": row.district,
        "regency": row.regency,
        "province": row.province,
        "phone": row.phone,
        "email": row.email,
        "tagline": row.tagline,
        "logo_url": row.logo_url,
        "signatory_left_title": row.signatory_left_title,
        "signatory_left_name": row.signatory_left_name,
        "signatory_mid_title": row.signatory_mid_title,
        "signatory_mid_name": row.signatory_mid_name,
        "signatory_right_title": row.signatory_right_title,
        "signatory_right_name": row.signatory_right_name,
        "primary_color": row.primary_color,
        "share_pengurus": float(row.share_pengurus),
        "share_penasihat": float(row.share_penasihat),
        "share_pengawas": float(row.share_pengawas),
        "share_dana_sosial": float(row.share_dana_sosial),
        "share_pades": float(row.share_pades),
        "share_modal_bumdes": float(row.share_modal_bumdes),
        "share_unit_pengelola": float(row.share_unit_pengelola),
        "share_unit_bumdes": float(row.share_unit_bumdes),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class OrgProfilePatch(BaseModel):
    org_name: Optional[str] = Field(None, min_length=1, max_length=255)
    org_legal_name: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    village: Optional[str] = Field(None, max_length=120)
    district: Optional[str] = Field(None, max_length=120)
    regency: Optional[str] = Field(None, max_length=120)
    province: Optional[str] = Field(None, max_length=120)
    phone: Optional[str] = Field(None, max_length=60)
    email: Optional[str] = Field(None, max_length=255)
    tagline: Optional[str] = Field(None, max_length=255)
    signatory_left_title: Optional[str] = Field(None, max_length=120)
    signatory_left_name: Optional[str] = Field(None, max_length=255)
    signatory_mid_title: Optional[str] = Field(None, max_length=120)
    signatory_mid_name: Optional[str] = Field(None, max_length=255)
    signatory_right_title: Optional[str] = Field(None, max_length=120)
    signatory_right_name: Optional[str] = Field(None, max_length=255)
    primary_color: Optional[str] = Field(None, min_length=6, max_length=7)
    share_pengurus: Optional[Decimal] = Field(None, ge=0, le=100)
    share_penasihat: Optional[Decimal] = Field(None, ge=0, le=100)
    share_pengawas: Optional[Decimal] = Field(None, ge=0, le=100)
    share_dana_sosial: Optional[Decimal] = Field(None, ge=0, le=100)
    share_pades: Optional[Decimal] = Field(None, ge=0, le=100)
    share_modal_bumdes: Optional[Decimal] = Field(None, ge=0, le=100)
    share_unit_pengelola: Optional[Decimal] = Field(None, ge=0, le=100)
    share_unit_bumdes: Optional[Decimal] = Field(None, ge=0, le=100)


@router.get("")
async def read_org_profile(
    _=Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    row = await get_org_profile(session)
    return _out(row)


@router.put("")
async def write_org_profile(
    payload: OrgProfilePatch,
    request: Request,
    admin=Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    fields = payload.model_dump(exclude_unset=True)
    if "primary_color" in fields and fields["primary_color"]:
        fields["primary_color"] = fields["primary_color"].lstrip("#").upper()

    # Validasi total 100% pakai nilai gabungan (existing + patch), bukan cuma
    # field yang dikirim di PATCH ini -- supaya PATCH parsial (mis. cuma ubah
    # share_pengurus saja) tetap divalidasi terhadap 5 angka lain yang sudah
    # tersimpan sebelumnya, bukan divalidasi seolah field lain itu 0.
    if any(f in fields for f in (*SHARE_FIELDS_BUMDES, *SHARE_FIELDS_UNIT)):
        current = await get_org_profile(session)
        merged = {f: fields.get(f, getattr(current, f)) for f in (*SHARE_FIELDS_BUMDES, *SHARE_FIELDS_UNIT)}
        validate_bagi_hasil_fields(merged)

    row = await update_org_profile(session, **fields)
    await record_audit(
        session, actor=admin, action="update_org_profile", entity="org_profiles",
        detail=", ".join(sorted(fields)), ip=_client_ip(request),
    )
    return _out(row)


@router.post("/logo")
async def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    admin=Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    data = await file.read()
    try:
        logo_url = await upload_org_logo(data, file.filename or "logo.png", file.content_type or "image/png")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Gagal upload logo: {exc}") from exc
    row = await update_org_profile(session, logo_url=logo_url)
    await record_audit(session, actor=admin, action="update_org_profile", entity="org_profiles", detail="Ganti logo", ip=_client_ip(request))
    return _out(row)
