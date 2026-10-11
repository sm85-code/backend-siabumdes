"""User-scoped delegation. A grant never carries the assigning Admin's privileges."""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update

from shared.config import public_role, REPORT_READ_LEVEL
from modules.siabumdes.identity.infrastructure.models import RiskaAssignment, RiskaDraft, User
from modules.siabumdes.infrastructure.models import UnitUsaha
from modules.siabumdes.identity.application.services import record_audit


async def current_assignment(session, user, *, lock=False):
    if public_role(user.role) == 'admin':
        return None
    statement = select(RiskaAssignment).where(RiskaAssignment.user_id == user.id)
    if lock:
        statement = statement.with_for_update()
    row = await session.scalar(statement.execution_options(populate_existing=True))
    if not row or not row.active or row.role != public_role(user.role) or row.unit_usaha_id != user.unit_usaha_id:
        raise HTTPException(403, 'Penugasan Riska tidak aktif atau hak akses pengguna berubah')
    if row.unit_usaha_id:
        unit = await session.get(UnitUsaha, row.unit_usaha_id)
        if not unit or not unit.active:
            raise HTTPException(403, 'Penugasan Riska/unit tidak aktif')
    return row


async def set_assignment(session, actor, recipient_id, allowed):
    if public_role(actor.role) != 'admin':
        raise HTTPException(403, 'Penugasan Riska hanya dapat diubah Admin')
    recipient = await session.scalar(select(User).where(User.id == recipient_id).with_for_update()
                                     .execution_options(populate_existing=True))
    if not recipient:
        raise HTTPException(404, 'Pengguna tidak ditemukan')
    if public_role(recipient.role) == 'admin':
        raise HTTPException(422, 'Admin selalu dapat memakai Riska tanpa penugasan')
    row = await session.scalar(select(RiskaAssignment).where(RiskaAssignment.user_id == recipient.id).with_for_update())
    if allowed:
        if not recipient.active or public_role(recipient.role) not in REPORT_READ_LEVEL:
            raise HTTPException(422, 'Pengguna/role tidak aktif atau tidak didukung')
        if public_role(recipient.role) == 'pengelola' and not recipient.unit_usaha_id:
            raise HTTPException(422, 'Pengelola belum memiliki unit usaha')
        if recipient.unit_usaha_id:
            unit = await session.get(UnitUsaha, recipient.unit_usaha_id)
            if not unit or not unit.active:
                raise HTTPException(422, 'Unit pengguna tidak aktif')
        if not row:
            row = RiskaAssignment(user_id=recipient.id)
            session.add(row)
        # Each explicit grant is a new session; older confirmations cannot revive.
        row.session_id = str(uuid.uuid4())
        row.role = public_role(recipient.role)
        row.unit_usaha_id = recipient.unit_usaha_id
        row.assigned_by = actor.id
        row.started_at = datetime.now(timezone.utc)
        row.ended_at = None
        row.active = True
        row.awaiting_finish = False
    elif row:
        row.active = False
        row.ended_at = datetime.now(timezone.utc)
    await session.execute(update(RiskaDraft).where(RiskaDraft.user_id == recipient.id,
                                                  RiskaDraft.consumed.is_(False)).values(consumed=True))
    await record_audit(session, actor=actor, action='riska_assignment', entity='users', entity_id=recipient.id,
                       detail=f'active={allowed}; role={recipient.role}; unit={recipient.unit_usaha_id}')
    await session.flush()
    return {'user_id': recipient.id, 'enabled': bool(row and row.active), 'session_id': row.session_id if row else None}


async def finish_assignment(session, user, session_id):
    if public_role(user.role) == 'admin':
        raise HTTPException(422, 'Admin tidak memiliki sesi penugasan sementara')
    row = await current_assignment(session, user, lock=True)
    if row.session_id != session_id:
        raise HTTPException(409, 'Sesi penugasan berubah. Muat sesi terbaru.')
    row.active = False
    row.ended_at = datetime.now(timezone.utc)
    await session.execute(update(RiskaDraft).where(RiskaDraft.user_id == user.id,
                                                  RiskaDraft.consumed.is_(False)).values(consumed=True))
    await record_audit(session, actor=user, action='riska_session_finish', entity='users', entity_id=user.id,
                       detail=f'session={session_id}')
    return {'enabled': False, 'session_ended': True,
            'message': 'Baik, sesi bantuan selesai. Terima kasih, Kak. Riska akan hadir kembali jika Admin menugaskan saya. 👩‍💼✨'}


async def bind_payload(session, user, payload):
    assignment = await current_assignment(session, user, lock=True)
    return {**payload, '_actor_role': public_role(user.role), '_session_id': assignment.session_id if assignment else None}


async def validate_draft_session(session, user, row):
    assignment = await current_assignment(session, user, lock=True)
    if row.payload.get('_actor_role') != public_role(user.role) or row.payload.get('_session_id') != (assignment.session_id if assignment else None):
        raise HTTPException(409, 'Hak akses atau sesi draf berubah. Buat draf baru.')


async def offer_more(session, user, result):
    assignment = await current_assignment(session, user, lock=True)
    if assignment:
        assignment.awaiting_finish = bool(not result.get('draft_id') and not result.get('conversation_id')
                                          and 'ada hal lain' in result.get('message', '').lower())
        await session.flush()
    return result
