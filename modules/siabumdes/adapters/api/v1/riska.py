"""Deterministic financial assistant. No external model calls."""
from datetime import date, datetime, timedelta, timezone
import re
from typing import Literal

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
    ai_mode: Literal['auto', 'internal', 'sol', 'sonnet'] = 'auto'
    ai_conversation_id: str | None = Field(default=None, max_length=64)


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
        return {"enabled": True, "session_id": assignment.session_id if assignment else None, 'ai_models': models}
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
    from modules.siabumdes.adapters.api.v1.riska_dialogue import store, expired
    from modules.siabumdes.identity.infrastructure.models import RiskaDraft
    from modules.siabumdes.application.riska_sessions import validate_draft_session
    history = None
    scope_id = user.unit_usaha_id if is_pengelola(user) else body.unit_usaha_id
    if not body.ai_conversation_id and body.ai_mode != 'internal':
        session_identity = assignment.session_id if assignment else None
        history = await session.scalar(select(RiskaDraft).where(
            RiskaDraft.user_id == user.id, RiskaDraft.unit_usaha_id == scope_id,
            RiskaDraft.consumed.is_(False), RiskaDraft.expires_at > datetime.now(timezone.utc),
            RiskaDraft.payload['kind'].as_string() == 'ai_history',
            RiskaDraft.payload['_actor_role'].as_string() == public_role(user.role),
            RiskaDraft.payload['auth_session_version'].as_integer() == user.session_version,
            RiskaDraft.payload['_session_id'].as_string() == session_identity,
        ).order_by(RiskaDraft.expires_at.desc()).limit(1))
    if body.ai_conversation_id:
        history = await session.scalar(select(RiskaDraft).where(RiskaDraft.id == body.ai_conversation_id, RiskaDraft.user_id == user.id).with_for_update())
        if not history or expired(history) or history.payload.get('kind') != 'ai_history':
            raise HTTPException(409, 'Percakapan AI sudah berakhir. Silakan kirim kembali permintaan Anda.')
        await validate_draft_session(session, user, history)
        if history.payload.get('auth_session_version') != user.session_version:
            raise HTTPException(409, 'Sesi login berubah. Mulai percakapan baru.')
        if history.unit_usaha_id != scope_id:
            raise HTTPException(409, 'Entitas percakapan berubah. Mulai percakapan baru untuk entitas yang dipilih.')
    if history:
        await validate_draft_session(session, user, history)
    from modules.siabumdes.application import riska_ai
    from .riska_crud import parse_command
    from modules.siabumdes.application.riska_language import intent
    provider = body.ai_mode if body.ai_mode in {'sol', 'sonnet'} else 'sol' if riska_ai.ready('sol') else 'sonnet'
    context = {'laporan': history.payload.get('report') if history else None, 'percakapan': history.payload.get('messages', []) if history else []}
    available_units = select(UnitUsaha).where(UnitUsaha.active.is_(True)).order_by(UnitUsaha.code).limit(100)
    if is_pengelola(user):
        available_units = available_units.where(UnitUsaha.id == user.unit_usaha_id)
    context['unit_tersedia'] = [{'code': unit.code, 'name': unit.name, 'business_type': unit.business_type} for unit in (await session.scalars(available_units)).all()]
    selected_entity = await session.get(UnitUsaha, scope_id) if scope_id else None
    if scope_id and (not selected_entity or not selected_entity.active):
        raise HTTPException(403, 'Entitas yang dipilih tidak aktif atau tidak tersedia.')
    context['entitas_terpilih'] = {'code': selected_entity.code, 'name': selected_entity.name} if selected_entity else {'code': 'BUMDES', 'name': 'BUMDES Pusat'}
    complex_answer = False
    if body.conversation_id:
        filling = await session.scalar(select(RiskaDraft).where(RiskaDraft.id == body.conversation_id, RiskaDraft.user_id == user.id).with_for_update())
        if filling and not expired(filling) and filling.payload.get('kind') == 'crud_dialogue':
            await validate_draft_session(session, user, filling)
            context['pengisian'] = {key: filling.payload.get(key) for key in ('resource', 'action', 'expecting', 'data')}
            complex_answer = bool(re.search(r'\b(?:sebenarnya|maksud|jadi|koreksi)\b', body.message, re.I) or len(body.message) > 40)
    if re.search(r'\b(?:transaksi|akun|coa)\b', body.message, re.I) and not body.conversation_id:
        from modules.siabumdes.infrastructure.models import Account, TransactionType
        from .riska_dialogue import resolve_units
        try:
            chosen, pusat = await resolve_units(session, body.message, scope_id, user.unit_usaha_id if is_pengelola(user) else None)
        except ValueError:
            chosen, pusat = [], True
        if len(chosen) <= 1:
            group = chosen[0].code if chosen else 'BUMDES'
            context['jenis_transaksi'] = [{'code': kind.code, 'name': kind.name, 'debit': kind.debit, 'kredit': kind.credit} for kind in (await session.scalars(select(TransactionType).where(TransactionType.group_code == group).limit(30))).all()]
            context['akun_tersedia'] = [{'code': account.code, 'name': account.name} for account in (await session.scalars(select(Account).where(Account.group_code == group, Account.active.is_(True)).limit(30))).all()]
    before = (user.role, user.unit_usaha_id, user.session_version)
    usage = None

    async def ask(data):
        response = await riska_ai.interpret(user, body.message, provider, data)
        await session.refresh(user)
        if not user.active or before != (user.role, user.unit_usaha_id, user.session_version):
            raise HTTPException(409, 'Hak akses berubah. Silakan masuk kembali sebelum melanjutkan.')
        await current_assignment(session, user, lock=True)
        return response

    async def dispatch(interpretation):
        if interpretation.action != 'command':
            if intent(body.message) in {'cash', 'profit'} and not body.conversation_id:
                return await handle_chat(body, user, session, request)
            return {'message': interpretation.reply or 'Boleh ceritakan bagian pekerjaan yang ingin saya bantu?', **({'conversation_id': body.conversation_id} if body.conversation_id else {})}
        command = parse_command(interpretation.command)
        action = intent(interpretation.command)
        write = command and command[1] != 'read' or action in {'masuk', 'keluar', 'rusak', 'imbal', 'close', 'lock', 'transfer'} or re.search(r'\b(?:aktifkan|nonaktifkan)\b', interpretation.command, re.I)
        requested_write = re.search(r'\b(?:tambah|tambahin|buat|bikinkan|bikinin|input|catat|masukkan|masukin|ubah|edit|koreksi|hapus|hapuskan|buang|aktifkan|nonaktifkan|tutup|kunci|transfer|bekukan|setor|bayar|beli|jual|rusak)\b', body.message, re.I)
        if write and (not requested_write or re.search(r'\b(?:jangan|bukan|tidak|batalkan)\b', body.message, re.I)):
            return {'message': 'Saya bisa membantu memeriksa datanya terlebih dahulu. Jika Anda ingin mengubah data, mohon sebutkan tindakan yang diinginkan; perubahan tetap menunggu konfirmasi Anda.'}
        if body.conversation_id and context.get('pengisian') and not write:
            return await handle_chat(body.model_copy(update={'message': interpretation.command, 'ai_mode': 'internal'}), user, session, request)
        if not (command or action != 'unknown' or re.search(r'\b(?:aktifkan|nonaktifkan)\b', interpretation.command, re.I)):
            return {'message': interpretation.reply or 'Boleh jelaskan data atau tindakan yang Anda maksud? Saya bantu langkah demi langkah.'}
        return await handle_chat(body.model_copy(update={'message': interpretation.command, 'ai_mode': 'internal'}), user, session, request)

    # Explicit model mode understands the whole request before internal dispatch.
    rich_instruction = not body.conversation_id and len(body.message) > 40 and re.search(r'\b(?:input|tambah|catat|buat|ubah|edit|hapus|masukin|masukkan)\b', body.message, re.I)
    language_needed = body.ai_mode in {'sol', 'sonnet'} and not body.conversation_id or complex_answer or rich_instruction
    if body.ai_mode != 'internal' and language_needed and intent(body.message) != 'cancel' and riska_ai.ready(provider):
        interpretation, usage = await ask(context)
        result = await dispatch(interpretation)
    else:
        result = await handle_chat(body, user, session, request)
        if result.get('unrecognized') and not result.get('conversation_id') and body.ai_mode != 'internal' and riska_ai.ready(provider):
            interpretation, usage = await ask(context)
            result = await dispatch(interpretation)
    source_report = None
    if result.get('rows') is not None:
        source_report = {'rows': result['rows'][:20], 'money_columns': result.get('money_columns', []), 'periode_dan_cakupan': result['message'], 'jumlah_baris': len(result['rows'])}
        wants_analysis = body.ai_mode in {'sol', 'sonnet'} or re.search(r'\b(?:analisis|analisa|evaluasi|ringkas|jelaskan)\b', body.message, re.I)
        if body.ai_mode != 'internal' and wants_analysis and riska_ai.ready(provider):
            try:
                explanation, usage = await ask({'unit_tersedia': context['unit_tersedia'], 'entitas_terpilih': context['entitas_terpilih'], 'laporan': source_report, 'percakapan': context['percakapan'], 'tugas': 'Jelaskan hasil pembacaan resmi ini; jangan meminta pembacaan atau tindakan lain.'})
                result['message'] += '\n\n' + explanation.reply
            except HTTPException as exc:
                if exc.status_code not in {429, 503}:
                    raise
                result['message'] += '\n\nLaporannya sudah tersedia. Bantuan penjelasan AI belum tersedia saat ini; data resmi tetap bisa Anda periksa di tabel.'
    if usage is not None:
        result['ai'] = usage
        messages = context['percakapan'] + [{'user': body.message, 'riska': result['message'][:1200]}]
        payload = {'kind': 'ai_history', 'messages': messages[-3:], 'report': source_report or context['laporan'], 'auth_session_version': user.session_version}
        if re.search(r'password|kata sandi|api[_ ]key', body.message, re.I):
            payload['messages'] = context['percakapan']
        if history:
            from modules.siabumdes.application.riska_sessions import bind_payload
            history.payload = await bind_payload(session, user, payload)
        else:
            history = await store(session, user, scope_id, payload, {})
        history.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        result['ai_conversation_id'] = history.id
    if result.get('rows') and 'Ada hal lain' not in result['message']:
        result['message'] += ' Ada hal lain yang bisa Riska bantu? ✨'
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
