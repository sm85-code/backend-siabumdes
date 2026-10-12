"""Conversational tool agent with explicit token-free commands and confirmed drafts."""
from datetime import date
import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import REPORT_READ_LEVEL, public_role
from shared.database import get_db
from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.adapters.api.scope import is_pengelola
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.application.riska_sessions import current_assignment, finish_assignment, offer_more
from modules.siabumdes.infrastructure.models import UnitUsaha

router = APIRouter(prefix="/api/riska", tags=["riska"])
READ = require_roles(*REPORT_READ_LEVEL)
DENIED = ("Akses ditolak. Tindakan operasional tutup buku, kunci periode, dan transfer bagi hasil "
          "mutlak hanya wewenang Admin Utama.")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    unit_usaha_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    ai_mode: Literal['auto', 'internal', 'sol', 'sonnet'] = 'internal'
    ai_conversation_id: str | None = Field(default=None, max_length=64)
    revision_draft_id: str | None = Field(default=None, max_length=64)
    operation_id: UUID | None = None


async def available(session, user):
    try:
        await current_assignment(session, user)
        return True
    except HTTPException:
        return False


@router.get("/availability")
async def availability(user: User = Depends(READ), session: AsyncSession = Depends(get_db)):
    try:
        assignment = await current_assignment(session, user)
        from modules.siabumdes.application import riska_ai
        models = {provider: {'available': riska_ai.ready(provider), 'model': riska_ai.os.getenv(settings[1], settings[2])} for provider, settings in riska_ai.MODELS.items()}
        return {"enabled": True, "session_id": assignment.session_id if assignment else None, 'ai_models': models, 'ai_budget': await riska_ai.budget_status(session)}
    except HTTPException:
        return {"enabled": False, "session_id": None}


class FinishIn(BaseModel):
    session_id: str


@router.post("/session/finish")
async def finish(body: FinishIn, user: User = Depends(READ), session: AsyncSession = Depends(get_db)):
    return await finish_assignment(session, user, body.session_id)


@router.get("/units")
async def units(user: User = Depends(READ), session: AsyncSession = Depends(get_db)):
    if not await available(session, user):
        raise HTTPException(403, "Penugasan Riska tidak aktif")
    stmt = select(UnitUsaha).where(UnitUsaha.active.is_(True)).order_by(UnitUsaha.code)
    if is_pengelola(user):
        stmt = stmt.where(UnitUsaha.id == user.unit_usaha_id)
    return [{"id": u.id, "code": u.code, "name": u.name, "business_type": u.business_type} for u in (await session.scalars(stmt)).all()]


@router.post("/chat")
async def chat(body: ChatIn, user: User = Depends(READ), session: AsyncSession = Depends(get_db), request: Request = None):
    if not await available(session, user):
        raise HTTPException(403, "Penugasan Riska tidak aktif")
    assignment = await current_assignment(session, user, lock=True)
    if assignment and body.session_id != assignment.session_id:
        raise HTTPException(409, "Sesi penugasan berubah. Buka kembali Riska.")
    if is_pengelola(user) and body.unit_usaha_id and body.unit_usaha_id != user.unit_usaha_id:
        raise HTTPException(403, 'Akses unit lain ditolak.')
    from modules.siabumdes.application.riska_language import intent
    if public_role(user.role) != 'admin' and intent(body.message) in {'close', 'lock', 'transfer'}:
        return await offer_more(session, user, {'message': DENIED})
    if assignment and getattr(assignment, "awaiting_finish", False) and body.message.strip().lower() in {"tidak", "nggak", "enggak", "tidak ada"}:
        return await finish_assignment(session, user, assignment.session_id)
    if assignment and re.fullmatch(r"(?:tidak[, ]+)?(?:selesai|sudah selesai|cukup|tidak ada lagi)[.! ]*", body.message.strip().lower()):
        return await finish_assignment(session, user, assignment.session_id)
    from modules.siabumdes.adapters.api.v1.riska_dialogue import handle_chat
    if body.ai_mode != 'internal':
        from modules.siabumdes.application.riska_agent import run
        result = await run(body, user, session, request)
    else:
        if body.revision_draft_id:
            raise HTTPException(422, 'Revisi percakapan memerlukan mode Agen AI.')
        result = await handle_chat(body, user, session, request)
        if result.get('rows') is not None and not result.get('draft_id'):
            from modules.siabumdes.application.riska_agent import remember_direct_report
            result = await remember_direct_report(body, user, session, result)
    return await offer_more(session, user, result)


class ComparisonIn(BaseModel):
    unit_ids: list[str] = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    include_pusat: bool = False


@router.post("/comparison")
async def comparison(body: ComparisonIn, user: User = Depends(require_roles("admin")),
                     session: AsyncSession = Depends(get_db)):
    """Admin-only analytical comparison; original ledgers are never merged."""
    if body.start_date > body.end_date or (body.end_date - body.start_date).days > 3660:
        raise HTTPException(422, "Rentang tanggal tidak valid")
    from modules.siabumdes.adapters.api.v1.riska_dialogue import comparative_report
    ids = set(body.unit_ids)
    selected = list((await session.scalars(select(UnitUsaha).where(UnitUsaha.id.in_(ids), UnitUsaha.active.is_(True))
                                          .order_by(UnitUsaha.code))).all())
    if len(selected) != len(ids):
        raise HTTPException(404, "Unit aktif tidak ditemukan")
    return await comparative_report(session, selected, body.include_pusat, body.start_date, body.end_date)
