"""Admin-only jejak aksi sensitif -- lihat identity/application/services.py
(record_audit) untuk apa saja yang dicatat dan kebijakan cakupannya."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.identity.application.services import audit_out, list_audit_log
from modules.siabumdes.identity.infrastructure.models import User
from shared.database import get_db

router = APIRouter(prefix="/api", tags=["audit-log"])


@router.get("/audit-log")
async def get_audit_log(
    response: Response,
    limit: int = Query(default=200, le=500),
    _: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    # Data admin-only yang sensitif -- jangan biarkan browser/proxy di depan
    # menyimpannya dan membalasnya lewat 304 ke request berikutnya (baik ke
    # admin yang sama dengan data basi, atau lebih parah ke user lain lewat
    # cache bersama yang tidak sadar cookie auth beda).
    response.headers["Cache-Control"] = "no-store"
    return [audit_out(r) for r in await list_audit_log(session, limit)]
