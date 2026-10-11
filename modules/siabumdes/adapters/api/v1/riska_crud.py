"""Explicit CRUD conversations over existing application workflows, never arbitrary SQL/tools.

Single-field replies allow names and ordinary Indonesian numbers/dates. Writes are
server-owned, expire, require confirmation and compare a locked target snapshot.
"""
import hashlib
import json
import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import inspect, or_, select
from sqlalchemy.exc import IntegrityError

from shared.config import PUBLIC_ROLES, WRITE_LEVEL, public_role
from modules.siabumdes.adapters.api.scope import assert_can_mutate_period, assert_unit_active, is_pengelola
from modules.siabumdes.application.riska_language import MONTHS, amount, clean, transaction_date
from modules.siabumdes.application.riska_sessions import bind_payload, validate_draft_session, offer_more
from modules.siabumdes.identity.infrastructure.models import RiskaDraft, User
from modules.siabumdes.infrastructure.models import Account, JournalItem, Transaction, TransactionType, UnitUsaha, YieldPartner, YieldPayment
from modules.siabumdes.inventory.infrastructure.models import Product, StockCategory, StockCard, StockAdjustment
from . import auth_router as auth, master_data_router as master, transaction_router as tx, uu05_inventory_router as inventory, yield_router as yields

MODELS = {'transaksi': Transaction, 'akun': Account, 'pengguna': User, 'unit': UnitUsaha,
          'produk': Product, 'mitra': YieldPartner, 'pembayaran': YieldPayment}
ADMIN = {'akun', 'pengguna', 'unit'}
LABELS = {'create': 'Tambah', 'update': 'Ubah', 'delete': 'Hapus', 'read': 'Lihat'}
FIELDS = {
 'transaksi': ['date', 'transaction_type', 'description', 'amount', 'debit_account_code', 'credit_account_code'],
 'akun': ['code', 'name', 'category', 'subcategory', 'normal_balance'],
 'pengguna': ['username', 'name', 'email', 'role'],
 'unit': ['code', 'name', 'business_type', 'clone_coa'],
 'produk': ['sku', 'name', 'category_id', 'unit_of_measure', 'cost_price', 'sell_price'],
 'mitra': ['name', 'capital'],
 'pembayaran': ['partner_id', 'year', 'month', 'transaction_date', 'amount'],
}
UPDATES = {
 'transaksi': FIELDS['transaksi'] + ['reference'], 'akun': ['name', 'category', 'subcategory', 'normal_balance'],
 'pengguna': ['username', 'name', 'email', 'role', 'unit_usaha_id'],
 'unit': ['name', 'business_type', 'description', 'revenue_scheme', 'active'],
 'produk': ['name', 'category_id', 'unit_of_measure', 'cost_price', 'sell_price'],
 'mitra': ['name', 'capital'], 'pembayaran': ['amount', 'transaction_date'],
}
NAMES = {
 'date': 'tanggal transaksi (hari ini / kemarin / 10 Oktober 2026)', 'transaction_date': 'tanggal transaksi (hari ini / kemarin / 10 Oktober 2026)',
 'transaction_type': 'jenis transaksi yang terdaftar', 'description': 'keterangan', 'amount': 'nominal (misalnya 300 ribu)',
 'debit_account_code': 'akun debit (nama akun atau kode)', 'credit_account_code': 'akun kredit (nama akun atau kode)',
 'code': 'kode', 'name': 'nama lengkap', 'category': 'kategori COA', 'subcategory': 'subkategori COA (atau kosong)',
 'normal_balance': 'saldo normal (debit / kredit)', 'username': 'username', 'email': 'email', 'role': 'role',
 'business_type': 'jenis usaha (jasa / perdagangan / manufaktur)', 'sku': 'SKU produk baru', 'category_id': 'kategori produk (nama atau kode)',
 'unit_of_measure': 'satuan barang (misalnya pcs)', 'cost_price': 'harga modal per barang', 'sell_price': 'harga jual per barang',
 'capital': 'modal mitra', 'partner_id': 'nama mitra', 'year': 'tahun pembayaran', 'month': 'bulan pembayaran',
 'unit_usaha_id': 'unit penugasan (nama / kode)', 'active': 'status unit (aktif / nonaktif)',
 'clone_coa': 'salin template COA sejenis (ya / tidak)', 'reference': 'referensi', 'revenue_scheme': 'skema pendapatan',
}


def parse_command(message):
    text = clean(message)
    match = re.search(r'\b(tambah|buat|input|catat|ubah|edit|koreksi|hapus|lihat|daftar|cari|aktifkan|nonaktifkan)\s+(?:data\s+)?(transaksi(?: umum)?|akun|coa|pengguna|user|unit(?: usaha)?|produk|mitra|pembayaran(?: imbal hasil)?)\b', text, re.I)
    if not match:
        return None
    verb, resource = match.group(1).lower(), match.group(2).lower()
    resource = {'coa': 'akun', 'user': 'pengguna'}.get(resource, resource.split()[0])
    action = 'create' if verb in {'tambah', 'buat', 'input', 'catat'} else 'update' if verb in {'ubah', 'edit', 'koreksi', 'aktifkan', 'nonaktifkan'} else 'delete' if verb == 'hapus' else 'read'
    tail = text[match.end():].strip(' :"')
    data = {'active': verb == 'aktifkan'} if verb in {'aktifkan', 'nonaktifkan'} and resource == 'unit' else {}
    # Do not infer a financial value from arbitrary digits in a name.
    return resource, action, tail, data


def authorize(user, resource, action):
    role = public_role(user.role)
    if role != 'admin' and (resource == 'pengguna' or resource in ADMIN and action != 'read'):
        raise HTTPException(403, 'Pengelolaan COA, pengguna, dan unit melalui Riska hanya untuk Admin Utama.')
    if resource in {'produk', 'mitra', 'pembayaran'} and role not in WRITE_LEVEL:
        raise HTTPException(403, 'Akses modul operasional tidak diizinkan untuk role Anda.')
    if action != 'read' and role not in WRITE_LEVEL:
        raise HTTPException(403, 'Role Anda hanya dapat membaca data.')
    if resource == 'transaksi' and action == 'delete' and role not in {'admin', 'direktur', 'bendahara'}:
        raise HTTPException(403, 'Penghapusan transaksi tidak diizinkan untuk role Anda.')


def scalar_values(row):
    return {attr.key: getattr(row, attr.key) for attr in inspect(type(row)).column_attrs}


def fingerprint(row):
    # Hash includes hidden fields for staleness detection; no password hashes in draft/display.
    return hashlib.sha256(json.dumps({k: format(v.normalize(), 'f') if isinstance(v, Decimal) else v for k, v in scalar_values(row).items()}, sort_keys=True, default=str).encode()).hexdigest()


def display(row):
    return {k: str(v) if v is not None else '' for k, v in scalar_values(row).items()
            if k not in {'password_hash', 'session_version', 'must_change_password', 'photo_url', 'updated_at'}}


async def lookup(session, model, needle, conditions=(), lock=False):
    columns = [getattr(model, name) for name in ['id', 'code', 'sku', 'username', 'name', 'reference', 'description'] if hasattr(model, name)]
    needle = needle.strip().strip('"')
    if not needle:
        raise ValueError('Sebutkan nama atau kode data yang dimaksud.')
    # Parameterized literal matching, with exact matches preferred over substrings.
    escaped = needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    statement = select(model).where(*conditions, or_(*(column.ilike(escaped, escape='\\') for column in columns)))
    rows = list((await session.scalars(statement)).all())
    if not rows:
        rows = list((await session.scalars(select(model).where(*conditions, or_(*(column.icontains(needle, autoescape=True) for column in columns))).limit(11))).all())
    if len(rows) != 1:
        choices = ', '.join(str(getattr(r, 'name', getattr(r, 'description', r.id))) + ' [' + r.id + ']' for r in rows[:10])
        raise ValueError(f'Data belum pasti. {"Pilihan: " + choices if choices else "Tidak ada data yang cocok."} Sebutkan nama lengkap atau pilih ID dari daftar.')
    if lock:
        return await session.get(model, rows[0].id, populate_existing=True, with_for_update=True)
    return rows[0]


async def unit_for(session, user, value, active=True):
    if is_pengelola(user):
        unit = await session.get(UnitUsaha, user.unit_usaha_id)
        if not unit:
            raise HTTPException(403, 'Unit pengelola tidak tersedia.')
        if value and value not in {unit.id, unit.code, unit.name}:
            candidate = await lookup(session, UnitUsaha, value)
            if candidate.id != unit.id:
                raise HTTPException(403, 'Akses unit lain ditolak.')
    elif value.casefold() in {'pusat', 'bumdes', 'bumdes pusat'}:
        return None
    else:
        unit = await lookup(session, UnitUsaha, value)
    if active and (not unit or not unit.active):
        raise ValueError('Unit harus aktif sebelum operasi ini dijalankan.')
    return unit


async def target(session, resource, data, unit, lock=False):
    if resource == 'pembayaran':
        return await session.scalar(select(YieldPayment).where(YieldPayment.partner_id == data['partner_id'], YieldPayment.year == int(data['year']), YieldPayment.month == int(data['month'])).with_for_update())
    conditions = []
    model = MODELS[resource]
    if resource == 'akun':
        conditions.append(Account.group_code == (unit.code if unit else 'BUMDES'))
    elif resource in {'transaksi', 'produk', 'mitra'}:
        conditions.append(model.unit_usaha_id == (unit.id if unit else None))
    return await lookup(session, model, data['target'], conditions, lock)


async def field_value(session, resource, field, message, unit):
    text = clean(message).strip().strip('"')
    if field in {'date', 'transaction_date'}:
        value = transaction_date(text, datetime.now(ZoneInfo('Asia/Jakarta')).date())
        if not value:
            raise ValueError('Tanggal belum jelas. Boleh jawab hari ini, kemarin, atau tanggal lengkap.')
        return value
    if field in {'amount', 'capital', 'cost_price', 'sell_price'}:
        return '0' if text in {'0', 'nol'} and field in {'cost_price', 'sell_price'} else amount(text)
    if field in {'year', 'month'}:
        value = MONTHS.index(text.lower()) + 1 if field == 'month' and text.lower() in MONTHS else int(text)
        if not (1900 <= value <= 9999 if field == 'year' else 1 <= value <= 12):
            raise ValueError('Tahun/bulan tidak valid.')
        return value
    if field == 'clone_coa':
        if text.lower() not in {'ya', 'tidak'}:
            raise ValueError('Jawab ya atau tidak.')
        return text.lower() == 'ya'
    if field == 'active':
        if text.lower() not in {'aktif', 'nonaktif', 'ya', 'tidak'}:
            raise ValueError('Pilih aktif atau nonaktif.')
        return text.lower() in {'aktif', 'ya'}
    if field in {'debit_account_code', 'credit_account_code'}:
        account = await lookup(session, Account, text, [Account.group_code == (unit.code if unit else 'BUMDES'), Account.active.is_(True)])
        return account.code
    if field == 'transaction_type':
        row = await lookup(session, TransactionType, text, [TransactionType.group_code == (unit.code if unit else 'BUMDES')])
        return row.code
    if field == 'category_id':
        return (await lookup(session, StockCategory, text, [])).id
    if field == 'partner_id':
        return (await lookup(session, YieldPartner, text, [YieldPartner.unit_usaha_id == unit.id])).id
    if field == 'unit_usaha_id':
        selected = await unit_for(session, type('Actor', (), {'role': 'admin'})(), text)
        return selected.id if selected else None
    if field == 'role':
        role = public_role(text.lower().replace(' ', '_'))
        if role not in PUBLIC_ROLES:
            raise ValueError('Pilih role: ' + ', '.join(sorted(PUBLIC_ROLES)))
        return role
    if field in {'business_type', 'normal_balance', 'category', 'subcategory'}:
        value = '' if text.lower() == 'kosong' else text.lower()
        if field == 'business_type' and value not in master.BUSINESS_TYPES:
            raise ValueError('Pilih jasa, perdagangan, atau manufaktur.')
        if field == 'normal_balance' and value not in {'debit', 'kredit'}:
            raise ValueError('Pilih debit atau kredit.')
        if field == 'category' and value not in master.categories_map():
            raise ValueError('Kategori COA tersedia: ' + ', '.join(master.categories_map()))
        return value
    if not text:
        raise ValueError('Jawaban belum diisi.')
    return text


async def question(session, user, state, field, row=None, extra=''):
    from .riska_dialogue import store
    state['expecting'] = field
    payload = json.loads(json.dumps({'kind': 'crud_dialogue', **state}, default=str))
    if row:
        row.payload = await bind_payload(session, user, payload)
    else:
        row = await store(session, user, state.get('unit_id'), payload, {})
    resource = state['resource']
    subject = resource + (' “' + str(state.get('data', {}).get('name')) + '”' if state.get('data', {}).get('name') else '')
    if field == 'entity':
        prompt = 'Boleh tahu data ini untuk BUMDES Pusat atau unit usaha mana? Pilih dari tombol di bawah, ya.'
    elif field == 'target':
        prompt = f'Boleh sebutkan {resource} yang ingin Anda pilih, memakai nama atau referensinya? Jika belum ingat, ketik “lihat {resource}” agar saya tampilkan daftarnya.'
    elif field in {'field', 'repair_field'}:
        prompt = 'Bagian mana yang ingin diubah? Pilih: ' + ', '.join(NAMES.get(k, k) for k in (FIELDS[resource] if field == 'repair_field' else UPDATES[resource])) + '.'
    else:
        prompt = 'Boleh saya tahu ' + NAMES.get(field, field) + '?'
    choices = [{'label': NAMES.get(key, key), 'message': key} for key in (FIELDS[resource] if field == 'repair_field' else UPDATES[resource])] if field in {'field', 'repair_field'} else []
    if field == 'entity':
        units = list((await session.scalars(select(UnitUsaha).where(UnitUsaha.active.is_(True)).order_by(UnitUsaha.code))).all())
        if resource in {'mitra', 'pembayaran'}:
            units = [unit for unit in units if unit.code == 'UU04']
        elif resource == 'produk':
            units = [unit for unit in units if unit.business_type in {'perdagangan', 'manufaktur'}]
        choices = ([] if resource in {'mitra', 'pembayaran', 'produk'} else [{'label': 'BUMDES Pusat', 'message': 'pusat'}]) + [{'label': unit.name, 'message': unit.code} for unit in units]
    if field in {'business_type', 'normal_balance', 'active', 'role'}:
        choices = [{'label': value, 'message': value} for value in ({'business_type': sorted(master.BUSINESS_TYPES), 'normal_balance': ['debit', 'kredit'], 'active': ['aktif', 'nonaktif'], 'role': sorted(PUBLIC_ROLES)}[field])]
    return {'conversation_id': row.id, 'choices': choices, 'message': f'Baik, saya bantu menyiapkan {subject}. {extra}{prompt} Jawab singkat saja; belum ada data yang disimpan.'}


def choose_field(resource, text):
    aliases = {'nama': 'name', 'email': 'email', 'username': 'username', 'role': 'role', 'nominal': 'amount',
               'tanggal': 'transaction_date' if resource == 'pembayaran' else 'date', 'keterangan': 'description',
               'harga modal': 'cost_price', 'harga jual': 'sell_price', 'kategori produk': 'category_id', 'kategori': 'category',
               'subkategori': 'subcategory', 'saldo normal': 'normal_balance', 'jenis usaha': 'business_type',
               'satuan': 'unit_of_measure', 'modal': 'capital', 'akun debit': 'debit_account_code', 'akun kredit': 'credit_account_code',
               'jenis transaksi': 'transaction_type', 'referensi': 'reference', 'status': 'active', 'unit': 'unit_usaha_id',
               'deskripsi': 'description', 'skema pendapatan': 'revenue_scheme'}
    text = text.lower().strip()
    value = aliases.get(text, text)
    if value not in UPDATES[resource]:
        raise ValueError('Bagian yang diubah belum dikenal. Pilih satu dari daftar.')
    return value


PATCH_WORDS = r'harga jual|harga modal|akun debit|akun kredit|jenis transaksi|saldo normal|jenis usaha|subkategori|kategori produk|skema pendapatan|nominal|keterangan|referensi|deskripsi|username|email|role|satuan|modal|status|nama'


def inline_patch(resource, text, require_target=False):
    # An explicit editable-field word separates the target from the new value.
    pattern = (r'\s+(' if require_target else r'^(') + PATCH_WORDS + r')\s+(?:(?:jadi|menjadi|ke)\s+)?(.+)$'
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    try:
        field = choose_field(resource, match.group(1))
    except ValueError:
        return None
    return text[:match.start()].strip().strip('"'), field, match.group(2).strip().strip('"')


async def validate(session, user, state, unit, existing):
    resource, action, data = state['resource'], state['action'], state['data']
    authorize(user, resource, action)
    if resource in {'produk', 'mitra', 'pembayaran'}:
        if not unit:
            raise ValueError('Modul ini memerlukan satu unit usaha.')
        if resource == 'produk' and unit.business_type not in {'perdagangan', 'manufaktur'}:
            raise ValueError('Produk hanya untuk unit perdagangan/manufaktur.')
        if resource in {'mitra', 'pembayaran'} and unit.code != 'UU04':
            raise ValueError('Register imbal hasil hanya tersedia pada unit UU04.')
    if action != 'read' and resource not in {'unit', 'pengguna'}:
        await assert_unit_active(session, unit.id if unit else None)
    if resource == 'transaksi':
        if existing:
            tx._assert_not_bagi_hasil(existing.reference)
            if existing.is_closing or existing.reference.startswith(('CLOSE-', 'stock-', 'purchase-pay:', 'sale-pay:')):
                raise ValueError('Transaksi generator harus dikoreksi melalui modul asalnya agar stok/pembayaran dan jurnal tetap sinkron.')
            await assert_can_mutate_period(session, user, existing.date, existing.unit_usaha_id)
        if action != 'delete':
            model = tx.TransactionIn(**data)
            tx._assert_not_bagi_hasil(model.reference)
            if model.reference.startswith(('CLOSE-', 'stock-', 'purchase-pay:', 'sale-pay:')):
                raise ValueError('Referensi generator hanya boleh dibuat oleh modul asalnya.')
            await assert_can_mutate_period(session, user, model.date, unit.id if unit else None)
            for key in ['debit_account_code', 'credit_account_code']:
                await lookup(session, Account, data[key], [Account.active.is_(True), Account.group_code == (unit.code if unit else 'BUMDES')])
            if data['debit_account_code'] == data['credit_account_code']:
                raise ValueError('Akun debit dan kredit harus berbeda.')
    if resource == 'akun':
        if action == 'delete':
            used = await session.scalar(select(JournalItem.id).where(JournalItem.account_id == existing.id).limit(1))
            used = used or await session.scalar(select(Transaction.id).where(Transaction.unit_usaha_id == (unit.id if unit else None), or_(Transaction.debit_account_code == existing.code, Transaction.credit_account_code == existing.code)).limit(1))
            used = used or await session.scalar(select(TransactionType.id).where(TransactionType.group_code == existing.group_code, or_(TransactionType.debit == existing.code, TransactionType.credit == existing.code)).limit(1))
            if used:
                raise ValueError('Akun sudah digunakan jurnal/jenis transaksi. Riwayat harus dipertahankan; akun ini tidak dapat dihapus.')
        else:
            master.AccountIn(**data)
            error = master._validate_category_pair(data['category'], data.get('subcategory', ''))
            if error or data['normal_balance'] not in {'debit', 'kredit'}:
                raise ValueError(error or 'Saldo normal harus debit atau kredit.')
    if resource == 'pengguna' and action != 'delete':
        if data.get('role') == 'pengelola':
            if not data.get('unit_usaha_id'):
                raise ValueError('Pengelola wajib memiliki unit penugasan.')
            await assert_unit_active(session, data['unit_usaha_id'])
        if action == 'create':
            auth.RegisterRequest(**data, password='generated-at-execution')
        else:
            auth.AdminUserUpdateRequest(**data)
    if resource == 'unit' and action != 'delete':
        (master.UnitIn if action == 'create' else master.UnitPatch)(**data)
        if data.get('business_type', 'jasa') not in master.BUSINESS_TYPES:
            raise ValueError('Jenis usaha harus jasa, perdagangan, atau manufaktur.')
    if resource == 'produk' and action == 'delete':
        if existing.qty_on_hand != 0 or await session.scalar(select(StockCard.id).where(StockCard.product_id == existing.id).limit(1)) or await session.scalar(select(StockAdjustment.id).where(StockAdjustment.product_id == existing.id).limit(1)):
            raise ValueError('Produk memiliki stok/riwayat mutasi; penghapusan tidak boleh menghapus jurnal historis.')
    if resource == 'produk' and action != 'delete':
        (inventory.ProductCreateBody if action == 'create' else inventory.ProductUpdateBody)(**data)
        if data.get('category_id'):
            await lookup(session, StockCategory, data['category_id'], [])
    if resource == 'mitra' and action != 'delete':
        yields.PartnerIn(**data)
    if resource == 'pembayaran':
        partner = await session.get(YieldPartner, data['partner_id'])
        if not partner or partner.unit_usaha_id != unit.id:
            raise HTTPException(403, 'Mitra bukan milik unit terpilih.')
        live = await target(session, resource, data, unit)
        if action == 'create' and live:
            raise HTTPException(409, 'Pembayaran sudah tercatat. Gunakan ubah pembayaran.')
        yields.PaymentIn(**data)
        if existing and existing.transaction_date:
            await assert_can_mutate_period(session, user, existing.transaction_date, unit.id)
        await assert_can_mutate_period(session, user, date.fromisoformat(str(data['transaction_date'])), unit.id)


async def related_records(session, resource, data, unit):
    records = []
    group = unit.code if unit else 'BUMDES'
    if resource == 'transaksi':
        kind = await session.scalar(select(TransactionType).where(TransactionType.code == data.get('transaction_type'), TransactionType.group_code == group))
        if kind:
            records.append(('transaction_type', kind))
        codes = [data['debit_account_code'], data['credit_account_code']]
    elif resource == 'pembayaran':
        from modules.siabumdes.application.yield_transactions import TYPE_CODE
        kind = await session.scalar(select(TransactionType).where(TransactionType.code == TYPE_CODE, TransactionType.group_code == group))
        if not kind or not kind.debit or not kind.credit:
            raise ValueError('Konfigurasi jenis pembayaran imbal hasil dan pasangan akun belum lengkap.')
        records.append(('transaction_type', kind))
        records.append(('partner', await session.get(YieldPartner, data['partner_id'])))
        codes = [kind.debit, kind.credit]
    else:
        codes = []
    for code in codes:
        records.append(('account', await lookup(session, Account, code, [Account.active.is_(True), Account.group_code == group])))
    if resource == 'unit' and data.get('clone_coa') and data.get('clone_from'):
        source = await lookup(session, UnitUsaha, data['clone_from'])
        records.append(('unit', source))
        for account in (await session.scalars(select(Account).where(Account.group_code == source.code).order_by(Account.id))).all():
            records.append(('account', account))
        for kind in (await session.scalars(select(TransactionType).where(TransactionType.group_code == source.code).order_by(TransactionType.id))).all():
            records.append(('transaction_type', kind))
    if resource == 'pengguna' and data.get('unit_usaha_id'):
        assigned_unit = await session.get(UnitUsaha, data['unit_usaha_id'])
        if assigned_unit:
            records.append(('unit', assigned_unit))
    if resource == 'produk' and data.get('category_id'):
        records.append(('category', await session.get(StockCategory, data['category_id'])))
    return records


async def handle_crud(session, user, body):
    from .riska_dialogue import expired, store, resolve_units
    command = parse_command(body.message)
    row = None
    if body.conversation_id:
        row = await session.scalar(select(RiskaDraft).where(RiskaDraft.id == body.conversation_id, RiskaDraft.user_id == user.id).with_for_update())
        if not row or row.payload.get('kind') != 'crud_dialogue':
            return None
        if expired(row):
            raise HTTPException(409, 'Percakapan kedaluwarsa. Mulai kembali.')
        await validate_draft_session(session, user, row)
        if re.fullmatch(r'(?:batal|batalkan|jangan)', clean(body.message), re.I):
            row.consumed = True
            return {'message': 'Baik, draf dibatalkan tanpa perubahan data.'}
        if command and re.match(r'^(?:(?:riska|tolong)[, ]+)*(?:tambah|buat|input|catat|ubah|edit|koreksi|hapus|lihat|daftar|cari|aktifkan|nonaktifkan)\s+', clean(body.message), re.I):
            row.consumed = True
            return await handle_crud(session, user, body.model_copy(update={'conversation_id': None}))
        state = {k: v for k, v in row.payload.items() if k != 'kind' and not k.startswith('_')}
    else:
        if not command:
            return None
        if re.search(r'\b(jangan|batal|batalkan|bukan)\b', body.message, re.I):
            return {'message': 'Baik, tidak ada perubahan data yang dijalankan.'}
        resource, action, tail, data = command
        authorize(user, resource, action)
        state = {'resource': resource, 'action': action, 'data': data, 'direct_status': 'active' in data}
        patch = inline_patch(resource, tail, True) if action == 'update' and resource != 'pembayaran' else None
        if patch:
            tail, field, value = patch
            state['inline_patch'] = {'field': field, 'value': value}
        if resource not in {'unit', 'pengguna'}:
            try:
                selected, pusat = await resolve_units(session, body.message, body.unit_usaha_id, user.unit_usaha_id if is_pengelola(user) else None)
            except ValueError as exc:
                return await question(session, user, state, 'entity', extra=f'{exc} ')
            if len(selected) > 1 or selected and pusat:
                return {'message': 'Pilih satu entitas untuk perubahan ini. Tidak ada data yang disimpan.'}
            if selected:
                state['unit_id'] = selected[0].id
                tail = re.sub(r'(?:^|\s+)(?:ke|di|untuk|pada)\s+(?:' + re.escape(selected[0].code) + '|' + re.escape(selected[0].name) + r')\b', '', tail, flags=re.I).strip()
            elif pusat:
                state['entity_selected'] = True
        if action == 'create' and resource == 'mitra' and tail:
            capital = re.search(r'\bmodal\s*(?:=|sebesar|senilai)?\s*(.+)$', tail, re.I)
            if capital:
                try:
                    state['data']['capital'] = str(amount(capital.group(1)))
                except ValueError:
                    pass
                tail = tail[:capital.start()].strip()
            name = re.sub(r'^baru\s+', '', tail, flags=re.I).strip().strip('"')
            if name:
                state['data']['name'] = name
        if action != 'create' and resource != 'pembayaran' and tail:
            state['data']['target'] = tail
        if is_pengelola(user):
            state['unit_id'] = user.unit_usaha_id
        elif body.unit_usaha_id and not state.get('unit_id') and not state.get('entity_selected'):
            state['unit_id'] = body.unit_usaha_id
    resource, action, data = state['resource'], state['action'], state['data']
    authorize(user, resource, action)
    unit = await session.get(UnitUsaha, state['unit_id']) if state.get('unit_id') else None
    existing = None
    try:
        if is_pengelola(user) and (body.unit_usaha_id and body.unit_usaha_id != user.unit_usaha_id or state.get('unit_id') != user.unit_usaha_id):
            raise HTTPException(403, 'Akses unit lain ditolak.')
        expecting = state.pop('expecting', None)
        if expecting == 'entity':
            unit = await unit_for(session, user, clean(body.message))
            state['unit_id'] = unit.id if unit else None
            state['entity_selected'] = True
        elif expecting == 'target':
            data['target'] = clean(body.message).strip('"')
        elif expecting in {'field', 'repair_field'}:
            value = clean(body.message)
            patch = inline_patch(resource, value)
            if patch:
                _, field, answer = patch
                state['inline_patch'] = {'field': field, 'value': answer}
                value = field
            state['update_field'] = value if expecting == 'repair_field' and value in FIELDS[resource] else choose_field(resource, value)
            state['value_supplied'] = False
            state['repairing'] = expecting == 'repair_field'
        elif expecting:
            data[expecting] = await field_value(session, resource, expecting, body.message, unit)
            state['value_supplied'] = True
        if resource not in {'unit', 'pengguna'} and not state.get('entity_selected') and not state.get('unit_id'):
            return await question(session, user, state, 'entity', row)
        wrong_entity = resource in {'mitra', 'pembayaran'} and (not unit or unit.code != 'UU04') or resource == 'produk' and (not unit or unit.business_type not in {'perdagangan', 'manufaktur'})
        if wrong_entity:
            if is_pengelola(user):
                raise HTTPException(403, 'Modul ini belum tersedia untuk unit Anda. Saya dapat membantu pekerjaan lain sesuai izin unit Anda.')
            return await question(session, user, state, 'entity', row, 'Modul ini memerlukan unit yang sesuai. Silakan pilih dari tombol berikut. ')
        if resource == 'unit' and action == 'read':
            statement = select(UnitUsaha)
            if is_pengelola(user):
                statement = statement.where(UnitUsaha.id == user.unit_usaha_id)
            elif public_role(user.role) != 'admin':
                statement = statement.where(UnitUsaha.active.is_(True))
        elif resource == 'pengguna' and action == 'read':
            statement = select(User)
        elif action == 'read':
            model = MODELS[resource]
            if resource == 'pembayaran':
                statement = select(YieldPayment).join(YieldPartner).where(YieldPartner.unit_usaha_id == (unit.id if unit else None))
            else:
                statement = select(model).where(Account.group_code == (unit.code if unit else 'BUMDES')) if resource == 'akun' else select(model).where(model.unit_usaha_id == (unit.id if unit else None))
        if action == 'read':
            if data.get('target'):
                model = MODELS[resource]
                columns = [getattr(model, key) for key in ['id', 'name', 'code', 'username', 'sku', 'description', 'reference'] if hasattr(model, key)]
                if columns:
                    statement = statement.where(or_(*(column.icontains(data['target'], autoescape=True) for column in columns)))
            records = list((await session.scalars(statement.order_by(MODELS[resource].id).limit(101))).all())
            if row:
                row.consumed = True
            output = [display(record) for record in records[:100]]
            if resource == 'pembayaran' and records:
                partners = {partner.id: partner.name for partner in (await session.scalars(select(YieldPartner).where(YieldPartner.id.in_([record.partner_id for record in records])))).all()}
                output = [{'mitra': partners.get(record.partner_id, ''), **display(record)} for record in records[:100]]
            return {'message': f'Berikut daftar {resource} sesuai akses Anda. ' + ('Ditampilkan maksimal 100 baris; persempit pencarian untuk data lain. ' if len(records) > 100 else '') + 'Ada hal lain yang bisa saya bantu? ✨', 'rows': output, 'money_columns': ['amount', 'capital', 'cost_price', 'sell_price']}
        existing = None
        if resource == 'pembayaran':
            for key in ['partner_id', 'year', 'month']:
                if key not in data:
                    return await question(session, user, state, key, row)
            existing = await target(session, resource, data, unit)
            if action != 'create' and not existing:
                raise ValueError('Pembayaran pada bulan/tahun tersebut belum tercatat.')
            if action == 'create' and existing:
                raise ValueError('Pembayaran sudah tercatat. Gunakan ubah pembayaran untuk mengoreksinya.')
        elif action != 'create':
            if 'target' not in data:
                return await question(session, user, state, 'target', row)
            existing = await target(session, resource, data, unit)
        if existing:
            state.setdefault('original', fingerprint(existing))
            if state['original'] != fingerprint(existing):
                raise HTTPException(409, 'Data berubah selama pengisian. Mulai kembali dan periksa data terbaru.')
            state['target_id'] = existing.id
            if action == 'update' and not state.get('loaded'):
                for key in UPDATES[resource]:
                    if key not in data and getattr(existing, key, None) is not None:
                        data[key] = getattr(existing, key)
                # Full official request models require original values for unchanged fields.
                if resource in {'transaksi', 'akun', 'mitra'}:
                    for key in FIELDS[resource]:
                        data.setdefault(key, getattr(existing, key, ''))
                state['loaded'] = True
            if action == 'delete' and resource == 'pembayaran':
                data['amount'] = str(existing.amount)
                data['transaction_date'] = (existing.transaction_date or datetime.now(ZoneInfo('Asia/Jakarta')).date()).isoformat()
            if action == 'delete' and resource == 'unit':
                data['active'] = False
        if state.get('inline_patch'):
            patch = state['inline_patch']
            data[patch['field']] = await field_value(session, resource, patch['field'], patch['value'], unit)
            state['update_field'] = patch['field']
            state['value_supplied'] = True
            state.pop('inline_patch')
        if state.get('repairing') and not state.get('value_supplied'):
            return await question(session, user, state, state['update_field'], row)
        if action == 'create':
            for key in FIELDS[resource]:
                if key not in data:
                    return await question(session, user, state, key, row)
        elif action == 'update' and not state.get('direct_status'):
            if not state.get('update_field'):
                return await question(session, user, state, 'field', row)
            if not state.get('value_supplied'):
                return await question(session, user, state, state['update_field'], row)
        if resource == 'pengguna' and data.get('role') == 'pengelola' and not data.get('unit_usaha_id'):
            return await question(session, user, state, 'unit_usaha_id', row)
        if resource == 'unit' and action == 'create' and data.get('clone_coa'):
            # Pin the live selected template, rather than resolve a different one at execution.
            template = await master.find_coa_template_unit(session, data['business_type'])
            if template:
                data['clone_from'] = template.code
        if resource == 'pembayaran':
            data['automatic'] = bool(existing.automatic) if existing and action == 'update' and state.get('update_field') != 'amount' else False
            if data['automatic']:
                partner = await session.get(YieldPartner, data['partner_id'])
                data['amount'] = str(yields.payment_amount(yields.PaymentIn(**data), partner))
        if resource == 'transaksi' and action == 'delete':
            for key in FIELDS[resource]:
                data[key] = str(getattr(existing, key))
        if resource == 'transaksi':
            data['unit_usaha_id'] = unit.id if unit else None
        if resource == 'akun':
            data['group'] = unit.code if unit else 'BUMDES'
        if resource == 'produk' and action == 'create':
            data['unit_usaha_id'] = unit.id
            data['opening_qty'] = 0  # Opening quantities belong to the journalled stock-in workflow.
        state['data'] = json.loads(json.dumps(data, default=str))
        data = state['data']
        await validate(session, user, state, unit, existing)
        # Convert date/Decimal model fields to JSON-safe canonical strings.
        state['data'] = json.loads(json.dumps(data, default=str))
        preview_rows = [{'bagian': NAMES.get(k, k), 'sebelum': str(getattr(existing, k, '')) if existing else '', 'sesudah': str(v)} for k, v in state['data'].items() if k not in {'target', 'partner_id', 'unit_usaha_id'} and (action != 'update' or not existing or str(getattr(existing, k, '')) != str(v))]
        if action == 'delete':
            preview_rows = [{'bagian': k, 'sebelum': v, 'sesudah': 'nonaktif' if resource == 'unit' else 'dihapus'} for k, v in display(existing).items() if k not in {'created_at'}]
        preview = {'unit': unit.name if unit else 'BUMDES Pusat', 'operation': 'Nonaktifkan unit' if resource == 'unit' and action == 'delete' else f'{LABELS[action]} {resource}', 'item': str(data.get('name', data.get('target', resource))), 'tanggal': str(data.get('date', data.get('transaction_date', '—'))), 'rows': preview_rows}
        if resource == 'pembayaran':
            partner = await session.get(YieldPartner, data['partner_id'])
            preview['item'] = partner.name
        if resource == 'pengguna' and data.get('unit_usaha_id'):
            assigned = await session.get(UnitUsaha, data['unit_usaha_id'])
            preview['details'] = {'Unit penugasan': assigned.name if assigned else 'Tidak ditemukan'}
        dependencies = await related_records(session, resource, state['data'], unit)
        state['dependencies'] = [{'resource': key, 'id': record.id, 'hash': fingerprint(record)} for key, record in dependencies]
        if resource == 'pembayaran':
            kind = next(record for key, record in dependencies if key == 'transaction_type')
            preview['rows'].append({'bagian': 'Jurnal terkait', 'sebelum': '', 'sesudah': f'Debit {kind.debit}, Kredit {kind.credit}, nominal {data["amount"]}'})
        state['unit_snapshot'] = fingerprint(unit) if unit else None
        draft = await store(session, user, unit.id if unit else None, {'kind': 'crud', **state}, preview)
        if row:
            row.consumed = True
        note = ' Kata sandi sementara dibuat setelah konfirmasi dan wajib diganti saat login.' if resource == 'pengguna' and action == 'create' else ''
        note += ' Riwayat unit tetap dipertahankan; unit akan dinonaktifkan.' if resource == 'unit' and action == 'delete' else ''
        return {'draft_id': draft.id, 'preview': preview, 'confirmation_level': 'admin' if public_role(user.role) == 'admin' else None, 'message': f'Draf {preview["operation"].lower()} siap. Periksa rincian sebelum memilih Ya, Eksekusi.{note}'}
    except (ValueError, ArithmeticError, ValidationError) as exc:
        if action == 'delete' and existing is not None:
            if row:
                row.consumed = True
            return {'message': f'Penghapusan belum dapat dilakukan: {str(exc)[:500]}. Tidak ada data yang diubah. Periksa melalui modul asal atau kirim perintah baru.'}
        return await question(session, user, state, expecting or state.get('update_field') or ('repair_field' if action == 'create' and all(k in data for k in FIELDS[resource]) else 'target' if action != 'create' else next((k for k in FIELDS[resource] if k not in data), FIELDS[resource][0])), row, f'Saya perlu memastikan datanya: {str(exc)[:500]}. ')


async def execute_crud(session, user, row, request):
    was_admin = public_role(user.role) == 'admin'
    state = row.payload
    resource, action = state['resource'], state['action']
    authorize(user, resource, action)
    if is_pengelola(user) and row.unit_usaha_id != user.unit_usaha_id:
        raise HTTPException(403, 'Akses unit lain ditolak.')
    unit = await session.get(UnitUsaha, row.unit_usaha_id, populate_existing=True, with_for_update=True) if row.unit_usaha_id else None
    if state.get('unit_snapshot') != (fingerprint(unit) if unit else None):
        raise HTTPException(409, 'Data unit berubah; buat dan periksa draf baru.')
    existing = await session.get(MODELS[resource], state['target_id'], populate_existing=True, with_for_update=True) if state.get('target_id') else None
    if state.get('target_id') and (not existing or fingerprint(existing) != state.get('original')):
        raise HTTPException(409, 'Data berubah sejak draf dibuat. Buat dan periksa draf baru.')
    data = {k: v for k, v in state['data'].items() if k != 'target'}
    checked = {**state, 'data': data}
    await validate(session, user, checked, unit, existing)
    for dependency in state.get('dependencies', []):
        model = {'transaction_type': TransactionType, 'account': Account, 'category': StockCategory, 'partner': YieldPartner, 'unit': UnitUsaha}[dependency['resource']]
        related = await session.get(model, dependency['id'], populate_existing=True, with_for_update=True)
        if not related or fingerprint(related) != dependency['hash']:
            raise HTTPException(409, 'Konfigurasi akun/jenis transaksi/data terkait berubah. Buat draf baru.')
    # Recheck absence/periods after dependency locks (official payment routes lock the partner).
    await validate(session, user, checked, unit, existing)
    fresh = await related_records(session, resource, data, unit)
    if [{'resource': key, 'id': related.id, 'hash': fingerprint(related)} for key, related in fresh] != state.get('dependencies', []):
        raise HTTPException(409, 'Daftar konfigurasi terkait berubah; buat draf baru.')
    request = request or Request({'type': 'http', 'headers': [], 'client': None})
    credentials = None
    try:
        async with session.begin_nested():
            if resource == 'transaksi':
                result = await tx.create_transaction(tx.TransactionIn(**data), user, session) if action == 'create' else await tx.update_transaction(existing.id, tx.TransactionIn(**data), user, session) if action == 'update' else await tx.delete_transaction(existing.id, user, session)
            elif resource == 'akun':
                result = await master.create_account(master.AccountIn(**data), request, user, session) if action == 'create' else await master.update_account(existing.code, master.AccountIn(**data), request, existing.group_code, user, session) if action == 'update' else await master.delete_account(existing.code, request, existing.group_code, user, session)
            elif resource == 'unit':
                result = await master.create_unit(master.UnitIn(**data), request, user, session) if action == 'create' else await master.update_unit(existing.id, master.UnitPatch(**data if action == 'update' else {'active': False}), request, user, session)
            elif resource == 'pengguna':
                if action == 'create':
                    password = secrets.token_urlsafe(18)
                    result = await auth.register(auth.RegisterRequest(**data, password=password), request, user, session)
                    credentials = {'username': result['username'], 'temporary_password': password}
                elif action == 'update':
                    result = await auth.admin_update_user(existing.id, auth.AdminUserUpdateRequest(**data), request, user, session)
                else:
                    if existing.id == user.id:
                        raise HTTPException(409, 'Tidak dapat menghapus akun yang sedang menjalankan sesi ini.')
                    result = await auth.delete_user(existing.id, request, user, session)
            elif resource == 'produk':
                result = await inventory.create_product(inventory.ProductCreateBody(**data), user, session) if action == 'create' else await inventory.update_product(existing.id, inventory.ProductUpdateBody(**data), user, session) if action == 'update' else await inventory.delete_product(existing.id, user, session)
            elif resource == 'mitra':
                result = await yields.create_partner(yields.PartnerIn(**data), request, unit, session, user) if action == 'create' else await yields.update_partner(existing.id, yields.PartnerIn(**data), request, unit, session, user) if action == 'update' else await yields.delete_partner(existing.id, request, unit, session, user)
            elif resource == 'pembayaran':
                model = yields.PaymentIn(**data)
                result = await yields.delete_payment(data['partner_id'], model, request, unit, session, user) if action == 'delete' else await yields.save_payment(data['partner_id'], model, request, unit, session, user)
            row.consumed = True
            from modules.siabumdes.identity.application.services import record_audit
            await record_audit(session, actor=user, action='riska_crud_execute', entity='riska_drafts', entity_id=row.id, detail=f'{action} {resource}')
            await session.flush()
    except IntegrityError:
        raise HTTPException(409, 'Data terkait masih digunakan atau kode/nama sudah terdaftar. Tidak ada perubahan tersimpan; periksa data dan buat draf baru.') from None
    reply = {'message': f'{LABELS[action]} {resource} berhasil. Ada hal lain yang bisa Riska bantu? ✨', 'refresh_required': True, 'credentials': credentials, 'auth_refresh_required': resource == 'pengguna' and existing is not None and existing.id == user.id, 'result': result}
    if was_admin and public_role(user.role) != 'admin':
        # The authorizing role applied to this action; future actions need a new assignment.
        return {**reply, 'session_ended': True}
    return await offer_more(session, user, reply)
