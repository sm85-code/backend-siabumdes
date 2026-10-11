"""Server-owned clarification, scoped analytics and confirmed Admin actions."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from sqlalchemy import select, text

from shared.config import public_role, WRITE_LEVEL
from modules.siabumdes.application.riska_sessions import bind_payload, validate_draft_session
from modules.siabumdes.application.riska_language import FIELDS, QUESTIONS, MONTHS, clean, extract_operation, intent, report_range
from modules.siabumdes.application.reporting import ReportingService
from modules.siabumdes.identity.infrastructure.models import RiskaDraft, ClosedPeriod
from modules.siabumdes.infrastructure.models import Account, UnitUsaha, YieldPartner, Transaction
from modules.siabumdes.inventory.infrastructure.models import Product, Vendor, Customer
from modules.siabumdes.money_json import money_str, stringify_money_fields

STRATEGIC = {'close': 'Tutup Buku', 'lock': 'Kunci Periode', 'transfer': 'Transfer Bagi Hasil'}
DENIED = ('Akses ditolak. Tindakan operasional tutup buku, kunci periode, dan transfer bagi hasil '
          'mutlak hanya wewenang Admin Utama.')


def today_jakarta():
    return datetime.now(ZoneInfo('Asia/Jakarta')).date()


def admin_user(user):
    return public_role(user.role) == 'admin'


def expired(row):
    return row.consumed or row.expires_at <= datetime.now(timezone.utc)


async def store(session, user, unit_id, payload, preview):
    payload = await bind_payload(session, user, payload)
    row = RiskaDraft(user_id=user.id, unit_usaha_id=unit_id, payload=payload, preview=preview,
                     expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    session.add(row)
    await session.flush()
    return row


async def resolve_units(session, message, selected_id=None, allowed_unit_id=None):
    """Resolve names only against live active records; no seeded/hardcoded name aliases."""
    statement = select(UnitUsaha).where(UnitUsaha.active.is_(True))
    if allowed_unit_id:
        statement = statement.where(UnitUsaha.id == allowed_unit_id)
    units = list((await session.scalars(statement.order_by(UnitUsaha.code))).all())
    lower = clean(message).lower()
    lower = re.sub(r'\bunit\s+(\d{1,2})\b', lambda match: 'uu' + match.group(1).zfill(2), lower)
    explicit_codes = set(re.findall(r'\buu\d+\b', lower))
    if explicit_codes - {u.code.lower() for u in units}:
        if allowed_unit_id:
            raise HTTPException(403, 'Akses unit lain ditolak')
        raise ValueError('Kode unit tidak aktif/tidak ditemukan. Pilih dari daftar unit aktif.')
    if re.search(r'\b(?:semua|seluruh) unit\b|\bper[_ ]unit\b', lower):
        return units, bool(re.search(r'\b(?:pusat|bumdes)\b', lower))
    selected = [u for u in units if re.search(rf'\b{re.escape(u.code.lower())}\b', lower)
                or u.name.lower() in lower]
    # A short unit name is acceptable only when it uniquely matches live names.
    fragments = re.findall(r'\bunit(?: usaha)?\s+(?!usaha\b|bulan\b)(.+?)(?=\s+(?:dan|bulan|periode|sebanyak|harga|tanggal|hari ini|20\d{2}|' + '|'.join(MONTHS) + r')\b|[?!,]|$)', lower)
    for fragment in fragments:
        fragment = fragment.strip()
        if fragment in {'ini', 'saya', 'terpilih', 'usaha', 'aktif'}:
            continue
        matches = [u for u in units if fragment == u.code.lower() or fragment in u.name.lower()]
        if len(matches) != 1:
            choices = ', '.join(f'{u.code}: {u.name}' for u in (matches or units))
            raise ValueError(f'Nama unit “{fragment}” belum dapat dipastikan. Sebutkan kode atau nama lengkap: {choices}.')
        if matches[0] not in selected:
            selected.append(matches[0])
    pusat = bool(re.search(r'\b(?:pusat|bumdes)\b', lower))
    if not selected and not pusat and selected_id:
        selected = [u for u in units if u.id == selected_id]
        if not selected:
            raise HTTPException(404, 'Unit aktif tidak ditemukan')
    return selected, pusat


async def clarify(session, user, unit_id, action, data, expecting, conversation=None, extra=''):
    payload = {'kind': 'dialogue', 'action': action, 'data': data, 'expecting': expecting}
    if conversation:
        conversation.payload = await bind_payload(session, user, payload)
        row = conversation
    else:
        row = await store(session, user, unit_id, payload, {})
    known = ', '.join(f'{k}: {v}' for k, v in data.items())
    return {'conversation_id': row.id, 'message': f'Baik, saya bantu siapkan {STRATEGIC.get(action, action)}. '
            f'{("Data sementara: " + known + ". ") if known else ""}{extra}{QUESTIONS[expecting]}',
            'expires_at': row.expires_at.isoformat()}


async def resolve_records(session, unit, action, data):
    """Exact ID/SKU or exact case-insensitive name, always within the selected unit."""
    lookup = [('mitra', YieldPartner)] if action == 'imbal' else [('produk', Product)]
    if action == 'masuk':
        lookup.append(('pemasok', Vendor))
    elif action == 'keluar':
        lookup.append(('customer', Customer))
    result = dict(data)
    for key, model in lookup:
        if key not in data:
            continue
        records = list((await session.scalars(select(model).where(model.unit_usaha_id == unit.id))).all())
        value = data[key].casefold()
        matches = [r for r in records if str(r.id).casefold() == value or r.name.casefold() == value
                   or (key == 'produk' and r.sku.casefold() == value)]
        if not matches:
            def simple_name(name):
                return re.sub(r'^(?:pak|bu|bapak|ibu|kak)\s+', '', name.casefold()).strip()
            needle = simple_name(data[key])
            if needle:
                matches = [r for r in records if re.search(rf'\b{re.escape(needle)}\b', simple_name(r.name))]
        if len(matches) != 1:
            candidates = matches or records[:10]
            choices = ', '.join(f'{r.name} ({r.sku if key == "produk" else r.id})' for r in candidates)
            return result, key, f'Data “{data[key]}” belum pasti di {unit.name}. Pilihan: {choices or "belum ada data terdaftar"}. '
        result[key] = matches[0].sku if key == 'produk' else matches[0].id
    return result, None, ''


async def invoke_strategy(session, user, payload, request):
    # Reuse the exact HTTP workflows, including audit and transfer-period guards.
    from modules.siabumdes.adapters.api.v1.period_close_router import (
        close_period, lock_period_endpoint, bagi_hasil_transfer,
        ClosePeriodRequest, LockPeriodRequest, BagiHasilTransferRequest,
    )
    action = payload['action']
    calls = {'close': (close_period, ClosePeriodRequest), 'lock': (lock_period_endpoint, LockPeriodRequest),
             'transfer': (bagi_hasil_transfer, BagiHasilTransferRequest)}
    handler, model = calls[action]
    request = request or Request({'type': 'http', 'headers': [], 'client': None})
    return await handler(model(period=payload['period'], group=payload['group']), request, user, session)


async def strategy_result(session, user, payload, request):
    result = await invoke_strategy(session, user, payload, request)
    postings = []
    if payload['action'] != 'lock':
        prefix = 'CLOSE-' if payload['action'] == 'close' else 'BAGIHASIL-'
        reference = f'{prefix}{payload["period"]}-{payload["group"]}'
        txs = (await session.scalars(select(Transaction).where(Transaction.reference == reference)
            .order_by(Transaction.date, Transaction.unit_usaha_id, Transaction.debit_account_code,
                      Transaction.credit_account_code, Transaction.amount))).all()
        postings = [{'tanggal': tx.date.isoformat(), 'entitas_id': tx.unit_usaha_id or 'BUMDES',
                     'debit': tx.debit_account_code, 'kredit': tx.credit_account_code, 'amount': money_str(tx.amount)}
                    for tx in txs]
    return {'result': result, 'postings': postings}


async def strategy_snapshot(session, user, payload, request):
    # Simulate inside a SAVEPOINT; no journals/status/audit persist.
    nested = await session.begin_nested()
    try:
        return await strategy_result(session, user, payload, request)
    finally:
        await nested.rollback()


async def strategic_draft(session, user, action, unit, message, request):
    if not admin_user(user):
        return {'message': DENIED}
    start, end = report_range(message, today_jakarta())
    if start.strftime("%Y-%m") != end.strftime("%Y-%m"):
        raise ValueError("Tindakan strategis memerlukan satu bulan, bukan rentang lintas bulan.")
    group = unit.code if unit else 'BUMDES'
    if action == 'lock' and re.search(r'\b(?:semua|seluruh) entitas\b', message.lower()):
        group = 'ALL'
    payload = {'kind': 'strategic', 'action': action, 'period': start.strftime('%Y-%m'), 'group': group}
    snapshot = await strategy_snapshot(session, user, payload, request)
    payload['snapshot'] = snapshot
    rows = snapshot['postings'] or [{'rincian': k, 'nilai': str(v)} for k, v in snapshot['result'].items() if not isinstance(v, (list, dict))]
    preview = {'unit': unit.name if unit else ('Seluruh entitas' if group == 'ALL' else 'BUMDES Pusat'),
               'operation': STRATEGIC[action], 'item': group, 'tanggal': payload['period'], 'rows': rows}
    row = await store(session, user, unit.id if unit else None, payload, preview)
    return {'draft_id': row.id, 'confirmation_level': 'admin', 'preview': preview,
            'message': f'⚠️ Peringatan Sistem: Anda akan mengeksekusi {STRATEGIC[action]} untuk '
                       f'{preview["unit"]}, periode {payload["period"]}. Tindakan ini memerlukan otorisasi Admin Utama. '
                       'Periksa rincian di bawah. Apakah Anda yakin?', 'expires_at': row.expires_at.isoformat()}


async def execute_strategy(session, user, row, request):
    if not admin_user(user):
        raise HTTPException(403, DENIED)
    payload = row.payload
    if row.unit_usaha_id:
        unit = await session.get(UnitUsaha, row.unit_usaha_id, with_for_update=True)
        if not unit or not unit.active or unit.code != payload['group']:
            raise HTTPException(409, 'Unit berubah/nonaktif; buat draf baru')
    # Serialize chat executions for the same entity and month; consumed row is also locked.
    await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))'),
                          {'scope': f'riska:{payload["group"]}:{payload["period"]}'})
    # Compare the actual postings before commit; a changed amount/COA rolls back the complete action.
    async with session.begin_nested():
        snapshot = await strategy_result(session, user, payload, request)
        if snapshot != payload['snapshot']:
            raise HTTPException(409, 'Data berubah sejak draf dibuat. Periksa dan konfirmasi draf baru.')
        row.consumed = True
    return {'message': f'{STRATEGIC[payload["action"]]} berhasil untuk {payload["group"]}, periode {payload["period"]}. '
                       'Ada data lain yang ingin Anda periksa? ✨', 'refresh_required': True, 'result': snapshot['result']}


async def comparative_report(session, units, pusat, start, end, cash=False):
    svc = ReportingService(session)
    rows = []
    totals = {'total_pendapatan': Decimal(0), 'total_hpp': Decimal(0), 'total_beban': Decimal(0), 'laba_bersih': Decimal(0)}
    for unit in ([None] if pusat else []) + list(units):
        unit_id, group, label = (unit.id, unit.code, unit.name) if unit else (None, 'BUMDES', 'BUMDES Pusat')
        if cash:
            stmt = select(Account).where(Account.active.is_(True), Account.subcategory == 'kas_bank', Account.group_code == group)
            stmt = stmt.where(Account.unit_usaha_id == unit_id) if unit else stmt.where(Account.unit_usaha_id.is_(None))
            for acc in (await session.scalars(stmt.order_by(Account.code))).all():
                data = await svc.ledger(acc.code, start, end, unit_id)
                rows.append({'entitas': label, 'akun': acc.name, 'saldo': money_str(data['saldo_akhir'])})
        else:
            data = await svc.laba_rugi(start, end, unit_id)
            closed = await session.scalar(select(ClosedPeriod.id).where(ClosedPeriod.period == end.strftime('%Y-%m'),
                                                                        ClosedPeriod.group_code == group))
            status = 'Tutup Buku' if closed else 'Terbuka'
            if (start.year, start.month) != (end.year, end.month):
                status = 'Bulan akhir: ' + status
            item = {'entitas': label, 'status': status}
            for key in totals:
                item[key] = money_str(data[key])
                totals[key] += Decimal(str(data[key]))
            revenue = Decimal(str(data['total_pendapatan']))
            costs = Decimal(str(data['total_hpp'])) + Decimal(str(data['total_beban']))
            item['margin_laba_persen'] = str((Decimal(str(data['laba_bersih'])) / revenue * 100).quantize(Decimal('0.01'))) if revenue > 0 else 'Tidak dapat dihitung'
            item['beban_dan_hpp_persen'] = str((costs / revenue * 100).quantize(Decimal('0.01'))) if revenue > 0 else 'Tidak dapat dihitung'
            item['akun_beban_utama'] = '; '.join(f'{row["name"]}: {money_str(row["amount"])}' for row in sorted(data.get('beban', []), key=lambda row: row['amount'], reverse=True)[:3]) or 'Tidak ada beban tercatat'
            item['akun_hpp_utama'] = '; '.join(f'{row["name"]}: {money_str(row["amount"])}' for row in sorted(data.get('hpp', []), key=lambda row: row['amount'], reverse=True)[:3]) or 'Tidak ada HPP tercatat'
            rows.append(item)
    if not cash:
        unit_rows = rows[int(pusat):]
        unit_revenue = sum((Decimal(row['total_pendapatan']) for row in unit_rows), Decimal(0))
        for row in rows:
            row['porsi_omzet_unit_persen'] = str((Decimal(row['total_pendapatan']) / unit_revenue * 100).quantize(Decimal('0.01'))) if row in unit_rows and unit_revenue > 0 else 'Tidak berlaku'
    multi = len(units) + int(pusat) > 1
    if multi:
        if cash:
            rows.append({'entitas': 'Total gabungan analitis', 'akun': 'Seluruh akun yang ditampilkan',
                         'saldo': money_str(sum((Decimal(row['saldo']) for row in rows), Decimal(0)))})
        else:
            rows.append({**({key: '—' for key in rows[0]} if rows else {}), 'entitas': 'Total gabungan analitis', 'status': 'Tanpa eliminasi antarentitas',
                         **{key: money_str(value) for key, value in totals.items()}})
    return stringify_money_fields({'message': f'Berikut laporan {start.isoformat()} sampai {end.isoformat()}. '
          + ('Gabungan ini adalah ringkasan analitis tanpa eliminasi antarentitas; bukan laporan konsolidasi akuntansi resmi. ' if multi else '')
          + ('Porsi omzet dihitung terhadap total pendapatan unit yang ditampilkan, bukan pendapatan Pusat. Rincian akun menunjukkan komponen biaya, bukan bukti penyebab operasional. ' if not cash else '')
          + 'Ada hal lain yang bisa Riska bantu? ✨', 'rows': rows,
          'money_columns': ['saldo', *totals]})


async def handle_chat(body, user, session, request=None):
    from modules.siabumdes.adapters.api.scope import is_pengelola
    from modules.siabumdes.adapters.api.v1.riska_execution import operation_unit, prepare, special_table
    message = clean(body.message)
    if body.conversation_id:
        pending = await session.scalar(select(RiskaDraft).where(RiskaDraft.id == body.conversation_id, RiskaDraft.user_id == user.id).with_for_update())
        if pending and pending.payload.get('kind') == 'report_dialogue':
            if expired(pending):
                raise HTTPException(409, 'Percakapan laporan kedaluwarsa. Silakan minta laporannya kembali.')
            await validate_draft_session(session, user, pending)
            pending.consumed = True
            if intent(message) == 'cancel':
                return {'message': 'Baik, permintaan laporan dibatalkan. Ada hal lain yang bisa saya bantu?'}
            combined = pending.payload['message'] + ' ' + message if intent(message) == 'unknown' else message
            return await handle_chat(body.model_copy(update={'message': combined, 'conversation_id': None, 'unit_usaha_id': pending.unit_usaha_id}), user, session, request)
    from modules.siabumdes.adapters.api.v1.riska_assignment import assignment_message
    assignment_reply = None if intent(message) == 'cancel' else await assignment_message(session, user, message)
    if assignment_reply is not None:
        return assignment_reply
    from .riska_crud import handle_crud
    crud_reply = await handle_crud(session, user, body)
    if crud_reply is not None:
        return crud_reply
    action = intent(message)
    if action in STRATEGIC and not admin_user(user):
        return {'message': DENIED}
    if is_pengelola(user) and re.search(r'\b(gabungan|konsolidasi|lintas|(?:semua|seluruh) unit|per[_ ]unit)\b', message.lower()):
        raise HTTPException(403, 'Pengelola hanya dapat mengakses unitnya sendiri.')
    if is_pengelola(user) and body.unit_usaha_id and body.unit_usaha_id != user.unit_usaha_id:
        raise HTTPException(403, 'Akses unit lain ditolak')
    conversation = None
    data = {}
    if body.conversation_id:
        conversation = await session.scalar(select(RiskaDraft).where(RiskaDraft.id == body.conversation_id,
                          RiskaDraft.user_id == user.id).with_for_update())
        if not conversation or expired(conversation) or conversation.payload.get('kind') != 'dialogue':
            raise HTTPException(409, 'Percakapan kedaluwarsa/tidak tersedia. Mulai perintah baru.')
        await validate_draft_session(session, user, conversation)
        if action == 'cancel':
            conversation.consumed = True
            return {'message': 'Baik, pengisian draf dibatalkan.'}
        if action not in {'unknown', conversation.payload['action']}:
            raise HTTPException(409, 'Selesaikan atau batalkan pengisian draf sebelum perintah baru.')
        action = conversation.payload['action']
        data = dict(conversation.payload['data'])
    if action == 'cancel':
        return {'message': 'Baik, tidak ada tindakan baru yang dijalankan.'}
    selected_id = conversation.unit_usaha_id if conversation else body.unit_usaha_id
    if is_pengelola(user):
        selected_id = user.unit_usaha_id
    try:
        selected, pusat = await resolve_units(session, message, selected_id, user.unit_usaha_id if is_pengelola(user) else None)
        if not admin_user(user):
            if is_pengelola(user) and (len(selected) > 1 or pusat or re.search(r'\b(gabungan|konsolidasi|lintas|semua unit|per[_ ]unit)\b', message.lower())):
                raise HTTPException(403, 'Pengelola hanya dapat mengakses unitnya sendiri.')
            if is_pengelola(user) and any(u.id != user.unit_usaha_id for u in selected):
                raise HTTPException(403, 'Akses unit lain ditolak')
        if action in FIELDS:
            if public_role(user.role) not in WRITE_LEVEL:
                raise HTTPException(403, 'Input Riska hanya untuk Admin atau pengelola yang ditugaskan.')
            expecting = conversation.payload['expecting'] if conversation else None
            data.update(extract_operation(message, action, today_jakarta(), expecting))
            if expecting == 'unit' and not selected:
                selected, pusat = await resolve_units(session, 'unit ' + message, allowed_unit_id=user.unit_usaha_id if is_pengelola(user) else None)
            if len(selected) != 1:
                return await clarify(session, user, None, action, data, 'unit', conversation)
            unit = await operation_unit(session, user, selected[0].id)
            if conversation:
                conversation.unit_usaha_id = unit.id
            data, missing, extra = await resolve_records(session, unit, action, data)
            missing = missing or next((key for key in FIELDS[action] if key not in data), None)
            if missing:
                return await clarify(session, user, unit.id, action, data, missing, conversation, extra)
            payload, preview = await prepare(session, user, unit, {'action': action, **data})
            row = await store(session, user, unit.id, payload, preview)
            if conversation:
                conversation.consumed = True
            return {'draft_id': row.id, 'preview': preview, 'message': 'Draf siap. Periksa barang/mitra, harga per barang, tanggal, dan pasangan jurnal sebelum konfirmasi.',
                    'expires_at': row.expires_at.isoformat()}
        if action in STRATEGIC:
            if len(selected) > 1:
                return {'message': 'Untuk keamanan, tindakan strategis dilakukan satu entitas per konfirmasi. Sebutkan satu unit atau BUMDES Pusat.'}
            return await strategic_draft(session, user, action, selected[0] if selected else None, message, request)
        start, end = report_range(message, today_jakarta())
        if len(selected) == 1 and action == 'unknown':
            table = await special_table(session, selected[0], message)
            if table:
                return table
        if action not in {'cash', 'profit'}:
            return {'unrecognized': True, 'message': 'Saya ingin memastikan maksud Anda agar tidak salah membantu. Apakah Anda ingin memeriksa laporan, mencatat data baru, atau mengubah data yang sudah ada? Ceritakan kebutuhan Anda, ya; kita selesaikan satu per satu.'}
        if admin_user(user) and not selected and not pusat and re.search(r'\b(gabungan|konsolidasi|lintas)\b', message.lower()):
            selected, _ = await resolve_units(session, 'semua unit')
        return await comparative_report(session, selected, pusat or not selected, start, end, action == 'cash')
    except (ValueError, ArithmeticError) as exc:
        if action in {'cash', 'profit'}:
            pending = await store(session, user, selected_id, {'kind': 'report_dialogue', 'message': message}, {})
            return {'conversation_id': pending.id, 'message': f'Baik, saya bantu memeriksa laporan tersebut. {exc} Anda boleh menjawab singkat, misalnya “tahun 2026” atau “September 2026”; saya tetap mengingat laporan yang Anda minta.'}
        return {'message': f'Saya perlu memastikan datanya: {exc}',
                **({'conversation_id': conversation.id} if conversation else {})}
