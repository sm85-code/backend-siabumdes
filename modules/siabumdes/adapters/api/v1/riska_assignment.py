"""Admin delegation drafts resolved against live users and units."""
import re
from fastapi import HTTPException
from sqlalchemy import select
from shared.config import public_role
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.infrastructure.models import UnitUsaha
from modules.siabumdes.application.riska_sessions import set_assignment


async def assignment_message(session, user, message):
    text = message.lower().strip()
    match = re.search(r'\b(aktifkan|nonaktifkan)\b.*\b(?:riska|dirimu|bantuan)\b', text)
    if not match:
        return None
    if public_role(user.role) != 'admin':
        raise HTTPException(403, 'Penugasan Riska hanya dapat diubah Admin')
    allowed = match.group(1) == 'aktifkan'
    users = list((await session.scalars(select(User).where(User.active.is_(True)).order_by(User.name))).all())
    users = [u for u in users if public_role(u.role) != 'admin']
    candidates = [u for u in users if re.search(rf'\b{re.escape(u.username.lower())}\b', text)
                  or re.search(rf'\b{re.escape(u.name.lower())}\b', text)]
    if not candidates:
        number = re.search(r'\bpengelola\s*(?:unit\s*)?(?:uu)?0?(\d+)\b', text)
        if number:
            code = 'UU' + number.group(1).zfill(2)
            unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.code == code, UnitUsaha.active.is_(True)))
            candidates = [u for u in users if public_role(u.role) == 'pengelola' and unit and u.unit_usaha_id == unit.id]
    if len(candidates) != 1:
        choices = candidates or users
        return {'message': 'Siapa pengguna yang akan dibantu? Sebutkan nama atau username lengkap agar tidak salah penugasan.',
                'rows': [{'Nama': u.name, 'Username': u.username, 'Role': public_role(u.role)} for u in choices[:30]]}
    recipient = candidates[0]
    from modules.siabumdes.adapters.api.v1.riska_dialogue import store
    payload = {'kind': 'assignment', 'recipient_id': recipient.id, 'allowed': allowed,
               'recipient_role': public_role(recipient.role), 'recipient_unit': recipient.unit_usaha_id}
    preview = {'unit': recipient.name, 'operation': 'Aktifkan bantuan' if allowed else 'Akhiri bantuan',
               'item': recipient.username, 'tanggal': '', 'rows': [{'Nama': recipient.name, 'Role': public_role(recipient.role),
               'Cakupan': 'Hak pengguna penerima; tanpa pemberian hak Admin'}]}
    draft = await store(session, user, None, payload, preview)
    return {'message': f'Baik, saya akan {"membantu" if allowed else "mengakhiri bantuan untuk"} {recipient.name}. '
                       'Hak akses tetap mengikuti pengguna tersebut. Silakan konfirmasi.',
            'draft_id': draft.id, 'confirmation_level': 'admin', 'preview': preview}


async def execute_assignment(session, user, draft):
    payload = draft.payload
    # Lock recipient before comparing the exact rights shown in the draft.
    recipient = await session.scalar(select(User).where(User.id == payload['recipient_id']).with_for_update()
                                     .execution_options(populate_existing=True))
    if not recipient or public_role(recipient.role) != payload['recipient_role'] or recipient.unit_usaha_id != payload['recipient_unit']:
        raise HTTPException(409, 'Pengguna/hak akses berubah. Buat draf penugasan baru.')
    result = await set_assignment(session, user, recipient.id, payload['allowed'])
    draft.consumed = True
    return {'message': f'Penugasan Riska untuk {recipient.name} sudah {"aktif" if result["enabled"] else "diakhiri"}. '
                       'Ada hal lain yang bisa Riska bantu? ✨', 'refresh_required': True}
