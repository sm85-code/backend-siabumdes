"""CRUD must use scoped official workflows and require one-time confirmed drafts."""
import os
from decimal import Decimal

import pytest

if not os.getenv('RISKA_TEST_DATABASE_URL'):
    pytest.skip('Requires isolated PostgreSQL', allow_module_level=True)

import test_riska_dialogue_pg as fixture_source  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select, func  # noqa: E402
from modules.siabumdes.adapters.api.v1.riska import chat, ChatIn  # noqa: E402
from modules.siabumdes.adapters.api.v1.riska_execution import execute, cancel  # noqa: E402
from modules.siabumdes.infrastructure.models import Account, Transaction, TransactionType, JournalEntry, JournalItem, UnitUsaha, YieldPartner, YieldPayment  # noqa: E402
from modules.siabumdes.identity.infrastructure.models import User, RiskaDraft, ClosedPeriod  # noqa: E402
from modules.siabumdes.inventory.infrastructure.models import Product  # noqa: E402


crud_context = fixture_source.context


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['tambah mitra SURYAMAN 5 jt', 'tambah mitra SURYAMAN modal 5jt'])
async def test_partner_name_and_capital_in_one_sentence(crud_context, command):
    session, admin, _, _, _, air, *_ = crud_context
    reply = await talk(session, admin, command, unit=air)
    assert reply.get('draft_id'), reply
    draft = await session.get(RiskaDraft, reply['draft_id'])
    assert draft.payload['data']['name'] == 'SURYAMAN'
    assert Decimal(draft.payload['data']['capital']) == Decimal('5000000')
    assert not await session.scalar(select(YieldPartner).where(YieldPartner.name == 'SURYAMAN'))


@pytest.mark.asyncio
@pytest.mark.parametrize('answer', ['itu, SURYAMAN, 5 jt', 'nama mitra SURYAMAN, modal 5 jt'])
async def test_partner_correction_updates_name_and_capital_together(crud_context, answer):
    session, admin, _, _, _, air, *_ = crud_context
    reply = await talk(session, admin, 'tambah mitra', ['Nama Salah'], air)
    reply = await chat(ChatIn(message=answer, conversation_id=reply['conversation_id'], unit_usaha_id=air.id), admin, session)
    assert reply.get('draft_id'), reply
    draft = await session.get(RiskaDraft, reply['draft_id'])
    assert draft.payload['data']['name'] == 'SURYAMAN'
    assert Decimal(draft.payload['data']['capital']) == Decimal('5000000')


@pytest.mark.asyncio
async def test_short_date_followup_remembers_day_month_and_entity(crud_context):
    session, admin, *_ = crud_context
    reply = await talk(session, admin, 'tambah transaksi', ['pusat', '1okt'])
    assert 'Tanggal 1 Oktober sudah saya pahami' in reply['message']
    assert reply['choices'] and all(len(choice['message']) == 4 for choice in reply['choices'])
    conversation = reply['conversation_id']
    repeated = await chat(ChatIn(message='1oktober', conversation_id=conversation), admin, session)
    assert repeated['conversation_id'] == conversation
    assert 'Untuk tahun berapa' in repeated['message']
    reply = await chat(ChatIn(message='2026', conversation_id=conversation), admin, session)
    row = await session.get(RiskaDraft, conversation)
    assert row.payload['data']['date'] == '2026-10-01'
    assert row.payload['entity_selected'] and row.payload['unit_id'] is None
    assert row.payload['expecting'] == 'transaction_type'
    assert not reply.get('draft_id')


async def talk(session, actor, command, answers=(), unit=None):
    kw = {'unit_usaha_id': unit.id if unit else None, 'session_id': 'manager-session' if actor.role == 'pengelola' else None}
    reply = await chat(ChatIn(message=command, **kw), actor, session)
    for answer in answers:
        assert reply.get('conversation_id'), reply
        reply = await chat(ChatIn(message=answer, conversation_id=reply['conversation_id'], **kw), actor, session)
    return reply


@pytest.mark.asyncio
async def test_product_update_scope_stale_cancel_and_delete(crud_context):
    session, admin, manager, other, unit, air, product, _ = crud_context
    draft = await talk(session, manager, 'ubah produk Daster Arab Jumbo', ['harga jual', '30 ribu'])
    assert draft.get('draft_id'), draft
    assert product.sell_price != Decimal('30000')
    with pytest.raises(HTTPException):
        await execute(draft['draft_id'], other, session)
    await execute(draft['draft_id'], manager, session)
    await session.refresh(product)
    assert product.sell_price == Decimal('30000')
    with pytest.raises(HTTPException):
        await execute(draft['draft_id'], manager, session)
    stale = await talk(session, admin, 'ubah produk Daster Arab Jumbo', ['nama', 'Daster Baru'], unit)
    product.sell_price = Decimal('40000')
    await session.flush()
    with pytest.raises(HTTPException) as error:
        await execute(stale['draft_id'], admin, session)
    assert error.value.status_code == 409
    cancelled = await talk(session, admin, 'ubah produk Daster Arab Jumbo', ['nama', 'Daster Baru'], unit)
    await cancel(cancelled['draft_id'], admin, session)
    assert product.name == 'Daster Arab Jumbo'
    refused = await talk(session, admin, 'hapus produk Daster Arab Jumbo', [], unit)
    assert not refused.get('draft_id') and 'riwayat' in refused['message']
    new = await talk(session, admin, 'tambah produk', ['SKU-NEW', 'Beras Desa', 'Test', 'kg', '10 ribu', '12 ribu'], unit)
    assert new.get('draft_id'), new
    await execute(new['draft_id'], admin, session)
    found = await session.scalar(select(Product).where(Product.sku == 'SKU-NEW'))
    assert found.qty_on_hand == 0
    deletion = await talk(session, admin, 'hapus produk Beras Desa', [], unit)
    await execute(deletion['draft_id'], admin, session)
    assert await session.get(Product, found.id) is None
    with pytest.raises(HTTPException):
        await talk(session, manager, 'lihat produk', [], air)


@pytest.mark.asyncio
async def test_coa_unit_user_crud_and_admin_guard(crud_context):
    session, admin, manager, other, unit, *_ = crud_context
    for command in ['tambah akun', 'lihat pengguna', 'hapus unit UU05']:
        with pytest.raises(HTTPException) as denied:
            await talk(session, manager, command)
        assert denied.value.status_code == 403
    draft = await talk(session, admin, 'tambah akun', ['9999', 'Akun Percobaan', 'aset', 'kosong', 'debit'], unit)
    assert draft.get('draft_id'), draft
    await execute(draft['draft_id'], admin, session)
    account = await session.scalar(select(Account).where(Account.code == '9999'))
    assert account.unit_usaha_id == unit.id
    changed = await talk(session, admin, 'ubah akun Akun Percobaan', ['nama', 'Akun Baru'], unit)
    await execute(changed['draft_id'], admin, session)
    removed = await talk(session, admin, 'hapus akun Akun Baru', [], unit)
    await execute(removed['draft_id'], admin, session)
    assert await session.get(Account, account.id) is None
    created = await talk(session, admin, 'tambah unit', ['UU99', 'Unit Baru', 'jasa', 'tidak'])
    await execute(created['draft_id'], admin, session)
    unit_change = await talk(session, admin, 'ubah unit Unit Baru', ['nama', 'Unit Baru Dua'])
    await execute(unit_change['draft_id'], admin, session)
    inactive = await talk(session, admin, 'hapus unit Unit Baru Dua')
    await execute(inactive['draft_id'], admin, session)
    new_unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.code == 'UU99'))
    assert not new_unit.active
    active = await talk(session, admin, 'aktifkan unit Unit Baru Dua')
    await execute(active['draft_id'], admin, session)
    assert new_unit.active
    userdraft = await talk(session, admin, 'tambah pengguna', ['baru', 'Staf Baru', 'baru@test.local', 'bendahara'])
    assert 'password' not in str((await session.get(RiskaDraft, userdraft['draft_id'])).payload)
    reply = await execute(userdraft['draft_id'], admin, session)
    assert reply['credentials']['temporary_password']
    target = await session.scalar(select(User).where(User.username == 'baru'))
    assert target.must_change_password
    changed = await talk(session, admin, 'ubah pengguna baru', ['nama', 'Nama Baru'])
    await execute(changed['draft_id'], admin, session)
    assert target.name == 'Nama Baru'
    deleted = await talk(session, admin, 'hapus pengguna baru')
    await execute(deleted['draft_id'], admin, session)
    assert await session.get(User, target.id) is None


@pytest.mark.asyncio
async def test_transaction_crud_journal_and_period(crud_context):
    session, admin, _, _, unit, *_ = crud_context
    session.add(TransactionType(code='sale', name='Penjualan', debit='1.1.01.15', credit='4.1.01.15', group_code=unit.code))
    await session.flush()
    count = await session.scalar(select(func.count()).select_from(Transaction))
    draft = await talk(session, admin, 'tambah transaksi', ['hari ini', 'Penjualan', 'Penjualan Baru', '50 ribu', '1.1.01.15', '4.1.01.15'], unit)
    assert draft.get('draft_id'), draft
    assert await session.scalar(select(func.count()).select_from(Transaction)) == count
    await execute(draft['draft_id'], admin, session)
    transaction = await session.scalar(select(Transaction).where(Transaction.description == 'Penjualan Baru'))
    changed = await talk(session, admin, 'ubah transaksi Penjualan Baru', ['nominal', '75 ribu'], unit)
    await execute(changed['draft_id'], admin, session)
    assert transaction.amount == Decimal('75000')
    entry = await session.scalar(select(JournalEntry).where(JournalEntry.transaction_id == transaction.id))
    items = list((await session.scalars(select(JournalItem).where(JournalItem.journal_entry_id == entry.id))).all())
    assert sum(i.amount for i in items if i.side == 'debit') == sum(i.amount for i in items if i.side == 'credit') == Decimal('75000')
    removed = await talk(session, admin, 'hapus transaksi Penjualan Baru', [], unit)
    await execute(removed['draft_id'], admin, session)
    assert await session.get(Transaction, transaction.id) is None
    old = await talk(session, admin, 'ubah transaksi Income', ['nominal', '70 ribu'], unit)
    session.add(ClosedPeriod(period='2026-09', group_code=unit.code, closed_by=admin.id))
    await session.flush()
    with pytest.raises(HTTPException):
        await execute(old['draft_id'], admin, session)


@pytest.mark.asyncio
async def test_payment_update_delete_paired_journal_and_conflict(crud_context):
    session, admin, _, _, _, unit, *_ = crud_context
    session.add(Account(code='4.1.01.24', name='Imbal Hasil', category='pendapatan', subcategory='pendapatan_operasional', normal_balance='kredit', group_code=unit.code, unit_usaha_id=unit.id))
    session.add(TransactionType(code='pendapatan_bagi_hasil_unit4', name='Imbal Hasil', debit='1.1.01.14', credit='4.1.01.24', group_code=unit.code))
    partner = YieldPartner(name='Bu Hajah Ani', capital=Decimal('1000000'), unit_usaha_id=unit.id)
    session.add(partner)
    await session.flush()
    new = await talk(session, admin, 'tambah pembayaran', ['Bu Hajah Ani', '2026', 'oktober', 'hari ini', '30 ribu'], unit)
    assert new.get('draft_id'), new
    await execute(new['draft_id'], admin, session)
    payment = await session.scalar(select(YieldPayment).where(YieldPayment.partner_id == partner.id))
    changed = await talk(session, admin, 'ubah pembayaran', ['Bu Hajah Ani', '2026', 'oktober', 'nominal', '40 ribu'], unit)
    assert changed.get('draft_id'), changed
    assert payment.amount == Decimal('30000')
    await execute(changed['draft_id'], admin, session)
    assert payment.amount == Decimal('40000')
    transaction = await session.get(Transaction, payment.transaction_id)
    assert transaction.amount == Decimal('40000')
    deletion = await talk(session, admin, 'hapus pembayaran', ['Bu Hajah Ani', '2026', 'oktober'], unit)
    await execute(deletion['draft_id'], admin, session)
    assert await session.get(YieldPayment, payment.id) is None
    assert await session.get(Transaction, transaction.id) is None


@pytest.mark.asyncio
async def test_inline_names_read_scopes_ambiguity_and_config_change(crud_context):
    session, admin, manager, _, unit, air, product, _ = crud_context
    inline = await talk(session, manager, 'Riska, ubah produk Daster Arab Jumbo harga jual jadi 35 ribu')
    assert inline.get('draft_id'), inline
    assert product.sell_price != Decimal('35000')
    await execute(inline['draft_id'], manager, session)
    assert product.sell_price == Decimal('35000')
    rows = await talk(session, manager, 'lihat transaksi')
    assert rows['rows'] and all(row['unit_usaha_id'] == unit.id for row in rows['rows'])
    assert not any('password_hash' in row for row in (await talk(session, admin, 'lihat pengguna'))['rows'])
    account = await session.scalar(select(Account).where(Account.group_code == unit.code, Account.code == '1.1.01.15'))
    new = await talk(session, admin, 'ubah transaksi Income', ['nominal', '80 ribu'], unit)
    account.name = 'Kas Berubah'
    await session.flush()
    with pytest.raises(HTTPException) as stale:
        await execute(new['draft_id'], admin, session)
    assert stale.value.status_code == 409
    account_delete = await talk(session, admin, 'hapus akun Kas Berubah', [], unit)
    assert not account_delete.get('draft_id') and 'digunakan' in account_delete['message']
    session.add(Product(sku='SKU-OTHER', name='Daster Arab Kecil', category_id=product.category_id, unit_usaha_id=unit.id, qty_on_hand=0))
    await session.flush()
    ambiguous = await talk(session, manager, 'hapus produk Daster Arab')
    assert not ambiguous.get('draft_id') and 'Pilihan' in ambiguous['message']
    negated = await talk(session, admin, 'jangan hapus pengguna manager')
    assert not negated.get('draft_id') and not negated.get('conversation_id')


@pytest.mark.asyncio
async def test_live_unit_template_and_partner_register_crud(crud_context):
    session, admin, _, _, unit, yield_unit, *_ = crud_context
    draft = await talk(session, admin, 'tambah unit', ['UU07', 'Toko Kedua', 'perdagangan', 'ya'])
    assert draft.get('draft_id') and any(row.get('sesudah') == unit.code for row in draft['preview']['rows']), draft
    await execute(draft['draft_id'], admin, session)
    new_unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.code == 'UU07'))
    accounts = list((await session.scalars(select(Account).where(Account.group_code == 'UU07'))).all())
    assert accounts and all(account.unit_usaha_id == new_unit.id for account in accounts)
    partner_draft = await talk(session, admin, 'tambah mitra', ['Pak Mitra Baru', '1 juta'], yield_unit)
    await execute(partner_draft['draft_id'], admin, session)
    partner = await session.scalar(select(YieldPartner).where(YieldPartner.name == 'Pak Mitra Baru'))
    update = await talk(session, admin, 'ubah mitra Pak Mitra Baru', ['modal', '2 juta'], yield_unit)
    await execute(update['draft_id'], admin, session)
    assert partner.capital == Decimal('2000000')
    delete = await talk(session, admin, 'hapus mitra Pak Mitra Baru', [], yield_unit)
    await execute(delete['draft_id'], admin, session)
    assert await session.get(YieldPartner, partner.id) is None


@pytest.mark.asyncio
async def test_admin_self_role_change_refreshes_and_revokes_old_drafts(crud_context):
    session, admin, _, _, unit, *_ = crud_context
    pending = await talk(session, admin, 'ubah produk Daster Arab Jumbo', ['harga jual', '45 ribu'], unit)
    draft = await talk(session, admin, 'ubah pengguna admin', ['role', 'bendahara'])
    assert draft.get('draft_id'), draft
    reply = await execute(draft['draft_id'], admin, session)
    assert reply['auth_refresh_required'] and reply['session_ended'] and admin.role == 'bendahara'
    with pytest.raises(HTTPException):
        await execute(pending['draft_id'], admin, session)


@pytest.mark.asyncio
async def test_new_command_replaces_pending_dialogue_and_inline_unit(crud_context):
    session, admin, _, _, toko, yield_unit, *_ = crud_context
    first = await talk(session, admin, 'hapus transaksi', [], toko)
    assert first.get('conversation_id')
    reply = await chat(ChatIn(message='tambah mitra ke UU04', conversation_id=first['conversation_id'], unit_usaha_id=toko.id), admin, session)
    assert reply.get('conversation_id') != first['conversation_id']
    assert 'nama lengkap' in reply['message']
    row = await session.get(RiskaDraft, reply['conversation_id'])
    assert row.unit_usaha_id == yield_unit.id
    reply = await chat(ChatIn(message='Bu Ani', conversation_id=reply['conversation_id'], unit_usaha_id=toko.id), admin, session)
    assert 'modal' in reply['message']
    reply = await chat(ChatIn(message='1 juta', conversation_id=reply['conversation_id'], unit_usaha_id=toko.id), admin, session)
    assert reply.get('draft_id'), reply
    assert not await session.scalar(select(YieldPartner).where(YieldPartner.name == 'Bu Ani'))


@pytest.mark.asyncio
async def test_model_interpretation_uses_scoped_drafts_and_no_implicit_write(crud_context, monkeypatch):
    from unittest.mock import AsyncMock
    from modules.siabumdes.application import riska_ai
    session, admin, manager, _, _, air, *_ = crud_context
    monkeypatch.setattr(riska_ai, 'ready', lambda provider: True)
    interpret = AsyncMock(return_value=(riska_ai.Interpretation(action='command', command='tambah mitra ke UU04', reply='Baik'), {'model': 'mock-sol'}))
    monkeypatch.setattr(riska_ai, 'interpret', interpret)
    reply = await chat(ChatIn(message='bikinin data baru dong', ai_mode='sol'), admin, session)
    assert reply.get('conversation_id') and 'nama lengkap' in reply['message']
    assert (await session.get(RiskaDraft, reply['conversation_id'])).unit_usaha_id == air.id
    assert not await session.scalar(select(YieldPartner).where(YieldPartner.name == 'Baru'))
    denied = await chat(ChatIn(message='boleh jelaskan sesuatu?', ai_mode='sol'), admin, session)
    assert not denied.get('conversation_id') and not denied.get('draft_id')
    with pytest.raises(HTTPException) as error:
        await chat(ChatIn(message='bikinin data baru dong', ai_mode='sol', session_id='manager-session'), manager, session)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_ai_budget_is_durable_and_provider_output_is_typed(crud_context, monkeypatch):
    import json
    import httpx
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from modules.siabumdes.application import riska_ai
    session, admin, *_ = crud_context
    await session.commit()
    monkeypatch.setattr(riska_ai, 'SessionLocal', async_sessionmaker(session.bind, expire_on_commit=False))
    monkeypatch.setenv('RISKA_AI_DAILY_USD', '0.06')
    ident = await riska_ai.reserve(admin.id, 'mock')
    assert ident
    with pytest.raises(HTTPException) as quota:
        await riska_ai.reserve(admin.id, 'mock')
    assert quota.value.status_code == 429
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(riska_ai, 'reserve', AsyncMock(return_value=ident))
    monkeypatch.setattr(riska_ai, 'record', AsyncMock())
    async def post(*args, **kwargs):
        assert kwargs['json']['store'] is False
        text = json.dumps({'action': 'clarify', 'command': '', 'reply': 'Boleh ceritakan kebutuhan Anda? Saya bantu, ya.'})
        return httpx.Response(200, request=httpx.Request('POST', 'https://api.openai.com/v1/responses'), json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}], 'usage': {'input_tokens': 40, 'output_tokens': 20}})
    monkeypatch.setattr(riska_ai.httpx.AsyncClient, 'post', post)
    result, usage = await riska_ai.interpret(admin, 'Bantu saya')
    assert result.action == 'clarify' and usage['input_tokens'] == 40
    riska_ai.record.assert_awaited_once_with(ident, 'succeeded', 40, 20)


@pytest.mark.asyncio
async def test_sonnet_protocol_and_invalid_output_fail_without_execution(crud_context, monkeypatch):
    import httpx
    from unittest.mock import AsyncMock
    from modules.siabumdes.application import riska_ai
    _, admin, *_ = crud_context
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-only')
    monkeypatch.setattr(riska_ai, 'reserve', AsyncMock(return_value='call'))
    monkeypatch.setattr(riska_ai, 'record', AsyncMock())
    async def post(*args, **kwargs):
        assert kwargs['headers']['anthropic-version'] == '2023-06-01'
        return httpx.Response(200, request=httpx.Request('POST', 'https://api.anthropic.com/v1/messages'), json={'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '```json\n{"action":"clarify","command":"","reply":"Boleh ceritakan kebutuhan Anda?"}\n```'}], 'usage': {'input_tokens': 20, 'output_tokens': 10}})
    monkeypatch.setattr(riska_ai.httpx.AsyncClient, 'post', post)
    result, usage = await riska_ai.interpret(admin, 'Bantu saya', 'sonnet')
    assert result.action == 'clarify' and usage['provider'] == 'sonnet'
    async def invalid(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request('POST', 'https://api.anthropic.com/v1/messages'), json={'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'not-json'}]})
    monkeypatch.setattr(riska_ai.httpx.AsyncClient, 'post', invalid)
    with pytest.raises(HTTPException) as error:
        await riska_ai.interpret(admin, 'Bantu saya', 'sonnet')
    assert error.value.status_code == 503
    assert riska_ai.record.call_args.args == ('call', 'failed')


@pytest.mark.asyncio
async def test_wrong_partner_entity_requests_entity_not_repair_fields(crud_context):
    session, admin, _, _, toko, air, *_ = crud_context
    reply = await talk(session, admin, 'tambah mitra', [], toko)
    assert reply.get('conversation_id') and [c['message'] for c in reply['choices']] == ['UU04']
    reply = await chat(ChatIn(message='UU04', conversation_id=reply['conversation_id'], unit_usaha_id=toko.id), admin, session)
    assert 'nama lengkap' in reply['message']
    direct = await talk(session, admin, 'tambah mitra Bu Ani modal 1 juta ke UU04', [], toko)
    assert direct.get('draft_id'), direct
    assert direct['preview']['unit'] == air.name


@pytest.mark.asyncio
async def test_report_period_followup_retains_original_question(crud_context):
    session, admin, _, _, toko, *_ = crud_context
    first = await talk(session, admin, 'laba rugi tahun', [], toko)
    assert first.get('conversation_id'), first
    reply = await chat(ChatIn(message='2026', conversation_id=first['conversation_id'], unit_usaha_id=toko.id, ai_mode='internal'), admin, session)
    assert reply.get('rows') is not None and not reply.get('conversation_id'), reply
    assert '2026-01-01' in reply['message']
    assert (await session.get(RiskaDraft, first['conversation_id'])).consumed


@pytest.mark.asyncio
async def test_ai_followup_has_official_report_and_owned_history(crud_context, monkeypatch):
    from unittest.mock import AsyncMock
    from modules.siabumdes.application import riska_ai
    session, admin, _, other, toko, *_ = crud_context
    monkeypatch.setattr(riska_ai, 'ready', lambda provider: True)
    mocked = AsyncMock(return_value=(riska_ai.Interpretation(action='explain', command='', reply='Berikut penjelasan laporan Anda.'), {'model': 'mock-sol'}))
    monkeypatch.setattr(riska_ai, 'interpret', mocked)
    first = await chat(ChatIn(message='jelaskan laba rugi tahun ini', unit_usaha_id=toko.id, ai_mode='sol'), admin, session)
    assert first.get('rows') and first.get('ai_conversation_id')
    follow = await chat(ChatIn(message='mengapa hasilnya begitu?', unit_usaha_id=toko.id, ai_mode='sol'), admin, session)
    context = mocked.call_args.args[3]
    assert context['laporan']['rows'] == first['rows']
    assert context['percakapan'][0]['user'] == 'jelaskan laba rugi tahun ini'
    assert follow['ai_conversation_id'] == first['ai_conversation_id']
    with pytest.raises(HTTPException):
        await chat(ChatIn(message='mengapa?', unit_usaha_id=toko.id, ai_mode='sol', ai_conversation_id=first['ai_conversation_id']), other, session)


@pytest.mark.asyncio
async def test_complete_transaction_instruction_does_not_reask_known_fields(crud_context, monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from modules.siabumdes.application import riska_ai
    session, admin, _, _, toko, *_ = crud_context
    session.add(TransactionType(code='sale', name='Penjualan', debit='1.1.01.15', credit='4.1.01.15', group_code=toko.code))
    await session.flush()
    command = 'tambah transaksi ' + json.dumps({'date': 'hari ini', 'transaction_type': 'Penjualan', 'description': 'Penjualan Baru', 'amount': '50 ribu', 'debit_account_code': '1.1.01.15', 'credit_account_code': '4.1.01.15', 'entitas': 'UU05'})
    monkeypatch.setattr(riska_ai, 'ready', lambda provider: True)
    monkeypatch.setattr(riska_ai, 'interpret', AsyncMock(return_value=(riska_ai.Interpretation(action='command', command=command, reply='Baik, saya siapkan drafnya.'), {'model': 'mock-sol'})))
    before = await session.scalar(select(func.count(Transaction.id)))
    reply = await chat(ChatIn(message='Input transaksi Penjualan hari ini, Penjualan Baru, nominal 50 ribu, debit 1.1.01.15 kredit 4.1.01.15 untuk UU05', ai_mode='auto', unit_usaha_id=toko.id), admin, session)
    assert reply.get('draft_id') and not reply.get('conversation_id'), reply
    assert await session.scalar(select(func.count(Transaction.id))) == before
    await execute(reply['draft_id'], admin, session)
    assert await session.scalar(select(func.count(Transaction.id))) == before + 1


@pytest.mark.asyncio
async def test_six_unit_analysis_keeps_central_revenue_separate(crud_context):
    session, admin, _, _, toko, air, *_ = crud_context
    for number in (1, 2, 3, 6):
        session.add(UnitUsaha(code=f'UU{number:02}', name=f'Unit Tes {number}', business_type='jasa', active=True))
    await session.flush()
    losing_unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.code == 'UU01'))
    session.add(Account(code='LOSS-EXP', name='Beban Operasional Tes', category='beban', subcategory='operasional', normal_balance='debit', group_code=losing_unit.code, unit_usaha_id=losing_unit.id))
    session.add(Account(code='LOSS-CASH', name='Kas Tes', category='aset', subcategory='kas_bank', normal_balance='debit', group_code=losing_unit.code, unit_usaha_id=losing_unit.id))
    from datetime import date
    session.add(Transaction(date=date(2026, 9, 15), amount=Decimal('100000'), transaction_type='expense', debit_account_code='LOSS-EXP', credit_account_code='LOSS-CASH', unit_usaha_id=losing_unit.id, description='Beban operasional', created_by=admin.id))
    await session.flush()
    reply = await chat(ChatIn(message='laba rugi semua unit dan pusat September 2026', ai_mode='internal'), admin, session)
    detail = [r for r in reply['rows'] if r['entitas'] not in {'BUMDES Pusat', 'Total gabungan analitis'}]
    assert len(detail) == 6
    losing = next(r for r in detail if r['entitas'] == losing_unit.name)
    assert Decimal(losing['laba_bersih']) == Decimal('-100000')
    assert 'Beban Operasional Tes' in losing['akun_beban_utama']
    assert Decimal(next(r for r in detail if r['entitas'] == air.name)['laba_bersih']) > 0
    assert sum(Decimal(r['porsi_omzet_unit_persen']) for r in detail if r['porsi_omzet_unit_persen'] != 'Tidak berlaku') == Decimal('100')
    central = next(r for r in reply['rows'] if r['entitas'] == 'BUMDES Pusat')
    assert central['porsi_omzet_unit_persen'] == 'Tidak berlaku'
    assert 'bukan pendapatan Pusat' in reply['message']


@pytest.mark.asyncio
async def test_casual_field_reply_keeps_already_supplied_partner_name(crud_context, monkeypatch):
    from unittest.mock import AsyncMock
    from modules.siabumdes.application import riska_ai
    session, admin, _, _, _, air, *_ = crud_context
    first = await talk(session, admin, 'tambah mitra', ['Bu Ani'], air)
    mocked = AsyncMock(return_value=(riska_ai.Interpretation(action='command', command='100 ribu', reply='Baik, modalnya saya sesuaikan.'), {'model': 'mock-sol'}))
    monkeypatch.setattr(riska_ai, 'ready', lambda provider: True)
    monkeypatch.setattr(riska_ai, 'interpret', mocked)
    reply = await chat(ChatIn(message='sebenarnya modalnya seratus ribu', conversation_id=first['conversation_id'], unit_usaha_id=air.id, ai_mode='auto'), admin, session)
    assert reply.get('draft_id') and not reply.get('conversation_id'), reply
    pending = mocked.call_args.args[3]['pengisian']
    assert pending['data']['name'] == 'Bu Ani' and pending['expecting'] == 'capital'
    assert (await session.get(RiskaDraft, reply['draft_id'])).payload['data']['capital'] == '100000'
