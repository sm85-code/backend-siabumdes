"""Finite typed tools; backend owns scopes, validation, arithmetic and drafts."""
from datetime import date
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from shared.config import public_role
from modules.siabumdes.adapters.api.scope import is_pengelola
from modules.siabumdes.infrastructure.models import UnitUsaha, Account, TransactionType


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Empty(Input):
    pass


class Entity(Input):
    entity: str | None = Field(default=None, max_length=64, description='ID atau kode unit dari daftar_unit; null berarti entitas pilihan pengguna.')


class Report(Input):
    report: Literal['profit_loss', 'cash_balance']
    scope: Literal['selected', 'all_units', 'units'] = 'selected'
    unit_ids: list[str] = Field(default_factory=list, max_length=100)
    include_central: bool = False
    start_date: date | None = None
    end_date: date | None = None
    year: int | None = Field(default=None, ge=2000, le=2099)
    last_months: int | None = Field(default=None, ge=1, le=120, description='Termasuk bulan berjalan, sampai hari ini; 6 bulan terakhir berarti bulan ini dan lima bulan sebelumnya.')

    @model_validator(mode='after')
    def period(self):
        if sum((bool(self.start_date or self.end_date), self.year is not None, self.last_months is not None)) > 1:
            raise ValueError('Pilih satu bentuk periode; koreksi periode mengganti nilai lama.')
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError('Tanggal awal dan akhir harus lengkap.')
        if self.scope == 'units' and not self.unit_ids:
            raise ValueError('Pilih unit dari daftar_unit.')
        if self.scope != 'units' and self.unit_ids:
            raise ValueError('unit_ids hanya untuk scope units.')
        return self

    def dates(self, today):
        if self.year:
            if self.year > today.year:
                raise ValueError('Tahun belum berjalan.')
            return date(self.year, 1, 1), today if self.year == today.year else date(self.year, 12, 31)
        if self.last_months:
            index = today.year * 12 + today.month - self.last_months
            return date(index // 12, index % 12 + 1, 1), today
        start, end = (self.start_date, self.end_date) if self.start_date else (today.replace(day=1), today)
        if start > end or (end - start).days > 3660 or end > today:
            raise ValueError('Rentang laporan harus sudah berjalan, berurutan, maksimal 10 tahun.')
        return start, end


class Crud(Entity):
    resource: Literal['transaksi', 'akun', 'pengguna', 'unit', 'produk', 'mitra', 'pembayaran']
    action: Literal['read', 'create', 'update', 'delete']
    target: str | None = Field(default=None, max_length=200, description='ID atau nama terdaftar; wajib untuk update/delete selain pembayaran.')
    fields: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=20, description='Field resmi dari informasi eksplisit pengguna; tanyakan nilai yang belum diberikan sebelum membuat draf.')


class Operation(Entity):
    action: Literal['masuk', 'keluar', 'rusak', 'imbal']
    fields: dict[str, str] = Field(max_length=12, description='Field sesuai formulir_operasional pada konteks.')


class Strategic(Entity):
    action: Literal['close', 'lock', 'transfer']
    period: str = Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')


class Assignment(Input):
    recipient: str = Field(min_length=1, max_length=64, description='ID atau username lengkap dari data_resmi pengguna.')
    allowed: bool


TOOLS = {
    'daftar_unit': (Empty, 'Daftar unit aktif yang boleh diakses; kode/ID untuk alat lain.'),
    'baca_laporan': (Report, 'Baca dan bandingkan laba rugi atau saldo kas resmi. Periode memakai tanggal, tahun, atau jumlah bulan terakhir. Semua angka dihitung backend.'),
    'referensi_transaksi': (Entity, 'Baca akun aktif dan jenis transaksi resmi pada satu entitas; jangan mengarang pasangan akun.'),
    'data_resmi': (Crud, 'Baca daftar/cari data atau siapkan draf tambah/ubah/hapus melalui validasi resmi. Tidak mengeksekusi. fields mengikuti formulir pada konteks.'),
    'draf_operasional': (Operation, 'Siapkan draf stok masuk/keluar/rusak atau setoran imbal hasil. Tidak mengeksekusi.'),
    'draf_penugasan': (Assignment, 'Admin saja: siapkan draf aktifkan/akhiri bantuan Riska bagi pengguna. Tidak memberi hak Admin; tetap membutuhkan konfirmasi.'),
    'draf_strategis': (Strategic, 'Admin saja: draf tutup buku/kunci periode/transfer bagi hasil satu entitas, satu bulan. Tidak mengeksekusi.'),
}


def definitions(user):
    return [{'name': name, 'description': description, 'input_schema': model.model_json_schema()} for name, (model, description) in TOOLS.items()
            if name not in {'draf_strategis', 'draf_penugasan'} or public_role(user.role) == 'admin']


async def accessible_units(session, user):
    statement = select(UnitUsaha).where(UnitUsaha.active.is_(True)).order_by(UnitUsaha.code)
    if is_pengelola(user):
        statement = statement.where(UnitUsaha.id == user.unit_usaha_id)
    return list((await session.scalars(statement)).all())


async def entity(session, user, value, selected_id):
    identifier = selected_id if value is None else value
    if is_pengelola(user):
        if identifier not in {None, user.unit_usaha_id}:
            unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.id == user.unit_usaha_id))
            if not unit or identifier != unit.code:
                raise HTTPException(403, 'Akses unit lain ditolak.')
        identifier = user.unit_usaha_id
    if identifier in {None, 'BUMDES'}:
        return None
    units = await accessible_units(session, user)
    matches = [u for u in units if identifier in {u.id, u.code}]
    if len(matches) != 1:
        raise HTTPException(422, 'Entitas tidak aktif/tidak ditemukan. Gunakan daftar_unit.')
    return matches[0]


async def execute(name, args, session, user, body, request=None, revision=None):
    from modules.siabumdes.adapters.api.v1 import riska_crud as crud, riska_dialogue as dialogue
    from modules.siabumdes.adapters.api.v1.riska_execution import prepare, operation_unit
    from modules.siabumdes.application.riska_language import FIELDS
    if name not in TOOLS:
        raise ValueError('Alat tidak tersedia.')
    data = TOOLS[name][0].model_validate(args)
    if revision and (name != 'data_resmi' or data.action != revision.payload['action'] or data.resource != revision.payload['resource']):
        raise HTTPException(422, 'Revisi hanya untuk tugas draf yang sama; batalkan draf untuk tugas lain.')
    if name == 'daftar_unit':
        return {'units': [{'id': u.id, 'code': u.code, 'name': u.name, 'business_type': u.business_type} for u in await accessible_units(session, user)]}
    if name == 'baca_laporan':
        units = await accessible_units(session, user)
        if is_pengelola(user) and (data.include_central or data.scope == 'units' and any(i != user.unit_usaha_id for i in data.unit_ids)):
            raise HTTPException(403, 'Akses laporan di luar unit Anda ditolak.')
        if data.scope == 'all_units':
            selected = units
        elif data.scope == 'units':
            selected = [u for u in units if u.id in data.unit_ids]
            if {u.id for u in selected} != set(data.unit_ids):
                raise HTTPException(403, 'Unit tidak aktif atau di luar akses Anda.')
        else:
            unit = await entity(session, user, None, body.unit_usaha_id)
            selected = [unit] if unit else []
        start, end = data.dates(dialogue.today_jakarta())
        result = await dialogue.comparative_report(session, selected, data.include_central or (data.scope == 'selected' and not selected), start, end, data.report == 'cash_balance')
        result['report_parameters'] = data.model_dump(mode='json')
        return result
    if name == 'draf_penugasan':
        from modules.siabumdes.identity.infrastructure.models import User
        from modules.siabumdes.adapters.api.v1.riska_assignment import prepare_assignment
        if public_role(user.role) != 'admin':
            raise HTTPException(403, 'Penugasan hanya untuk Admin.')
        recipient = await session.scalar(select(User).where((User.id == data.recipient) | (User.username == data.recipient)))
        if not recipient:
            raise ValueError('Pengguna tidak ditemukan. Baca daftar pengguna terlebih dahulu.')
        return await prepare_assignment(session, user, recipient, data.allowed)
    unit = await entity(session, user, data.entity, body.unit_usaha_id)
    if revision and (unit.id if unit else None) != revision.unit_usaha_id:
        raise HTTPException(422, 'Entitas revisi harus tetap sama dengan draf awal.')
    if name == 'referensi_transaksi':
        group = unit.code if unit else 'BUMDES'
        return {'accounts': [{'code': a.code, 'name': a.name} for a in (await session.scalars(select(Account).where(Account.group_code == group, Account.active.is_(True)).limit(100))).all()],
                'transaction_types': [{'code': t.code, 'name': t.name, 'debit': t.debit, 'credit': t.credit} for t in (await session.scalars(select(TransactionType).where(TransactionType.group_code == group).limit(100))).all()]}
    if name == 'data_resmi':
        crud.authorize(user, data.resource, data.action)
        allowed = set(crud.FIELDS[data.resource] if data.action == 'create' else crud.UPDATES[data.resource])
        if data.resource == 'pembayaran':
            allowed |= {'partner_id', 'year', 'month'}
        if set(data.fields) - allowed:
            raise ValueError('Field tidak sesuai formulir resmi: ' + ', '.join(sorted(set(data.fields) - allowed)))
        if data.action == 'read' and data.fields:
            raise ValueError('Pembacaan tidak menerima perubahan field.')
        fields = {**revision.payload['data'], **data.fields} if revision else data.fields.copy()
        fields = {k: v for k, v in fields.items() if k in allowed}
        state = {'resource': data.resource, 'action': data.action, 'data': {'target': data.target} if data.target else {},
                 'supplied': fields, 'entity_selected': True, 'unit_id': unit.id if unit else None}
        if data.action == 'update' and fields:
            state.update(update_field=next(iter(fields)), value_supplied=True)
        result = await crud.handle_crud(session, user, body.model_copy(update={'conversation_id': None}), structured_state=state)
        if result.get('conversation_id'):
            pending = await session.get(dialogue.RiskaDraft, result.pop('conversation_id'))
            result['missing_field'] = pending.payload.get('expecting')
            result['task'] = pending.payload
            pending.consumed = True  # Agent owns clarification; do not start a second parser dialogue.
        if result.get('draft_id'):
            result['can_revise'] = data.action == 'create' and data.resource in {'mitra', 'transaksi'}
            if revision:
                revision.consumed = True
        return result
    if name == 'draf_strategis':
        if public_role(user.role) != 'admin':
            raise HTTPException(403, 'Tindakan strategis hanya untuk Admin.')
        return await dialogue.strategic_draft(session, user, data.action, unit, data.period, request)
    if not unit:
        raise ValueError('Operasional memerlukan satu unit aktif.')
    unit = await operation_unit(session, user, unit.id)
    if set(data.fields) != set(FIELDS[data.action]):
        raise ValueError('Lengkapi field operasional: ' + ', '.join(FIELDS[data.action]))
    resolved, missing, extra = await dialogue.resolve_records(session, unit, data.action, data.fields)
    if missing:
        return {'message': extra, 'missing_field': missing}
    payload, preview = await prepare(session, user, unit, {'action': data.action, **resolved})
    draft = await dialogue.store(session, user, unit.id, payload, preview)
    return {'draft_id': draft.id, 'preview': preview, 'message': 'Draf operasional siap. Periksa rincian sebelum konfirmasi.'}
