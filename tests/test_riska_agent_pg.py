"""ERP-style tool agent acceptance tests against isolated PostgreSQL schemas."""
import json
import os
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

if not os.getenv('RISKA_TEST_DATABASE_URL'):
    pytest.skip('Requires isolated PostgreSQL', allow_module_level=True)

import test_riska_dialogue_pg as fixture_source  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from modules.siabumdes.adapters.api.v1.riska import chat, ChatIn  # noqa: E402
from modules.siabumdes.adapters.api.v1.riska_execution import execute  # noqa: E402
from modules.siabumdes.application import riska_agent_provider, riska_ai  # noqa: E402
from modules.siabumdes.identity.infrastructure.models import RiskaDraft  # noqa: E402
from modules.siabumdes.infrastructure.models import YieldPartner  # noqa: E402

context = fixture_source.context
USAGE = {'provider': 'sol', 'model': 'mock-sol', 'input_tokens': 100, 'output_tokens': 20}


def tool(name, args):
    return ([{'type': 'tool_use', 'id': 'tool-1', 'name': name, 'input': args}], USAGE)


def answer(text):
    return ([{'type': 'text', 'text': text}], USAGE)


def model(monkeypatch, steps):
    monkeypatch.setattr(riska_ai, 'ready', lambda provider: True)
    mocked = AsyncMock(side_effect=steps)
    monkeypatch.setattr(riska_agent_provider, 'call', mocked)
    return mocked


@pytest.mark.asyncio
async def test_human_report_period_correction_replaces_previous_period(context, monkeypatch):
    session, admin, _, other, *_ = context
    mocked = model(monkeypatch, [
        tool('baca_laporan', {'report': 'profit_loss', 'scope': 'all_units', 'last_months': 6}),
        answer('Berikut perbandingan enam bulan terakhir.'),
        tool('baca_laporan', {'report': 'profit_loss', 'scope': 'all_units', 'year': 2026}),
        answer('Periode saya ganti menjadi tahun 2026.'),
    ])
    first = await chat(ChatIn(message='bandingkan laba rugi unit usaha aktif 6 bulan terakhir', ai_mode='auto'), admin, session)
    assert first.get('rows') and not first.get('conversation_id')
    assert first['report_parameters']['last_months'] == 6
    revised = await chat(ChatIn(message='tahun 2026', ai_mode='auto', ai_conversation_id=first['ai_conversation_id']), admin, session)
    assert revised.get('rows') and revised['report_parameters']['year'] == 2026
    assert revised['report_parameters']['last_months'] is None
    assert 'enam bulan' in json.dumps(mocked.call_args.args[2], ensure_ascii=False)
    assert revised['ai']['calls'] == 2
    with pytest.raises(HTTPException) as error:
        await chat(ChatIn(message='lanjut', ai_mode='auto', ai_conversation_id=first['ai_conversation_id']), other, session)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_short_answer_retains_task_then_draft_requires_confirmation(context, monkeypatch):
    session, admin, _, _, _, unit, *_ = context
    mocked = model(monkeypatch, [
        tool('data_resmi', {'resource': 'mitra', 'action': 'create', 'entity': unit.id, 'fields': {'name': 'Suryaman'}}),
        answer('Berapa modal Suryaman?'),
        tool('data_resmi', {'resource': 'mitra', 'action': 'create', 'entity': unit.id, 'fields': {'name': 'Suryaman', 'capital': '5 juta'}}),
        tool('data_resmi', {'resource': 'mitra', 'action': 'create', 'entity': unit.id, 'fields': {'capital': '6 juta'}}),
    ])
    first = await chat(ChatIn(message='Tambah mitra Suryaman', unit_usaha_id=unit.id, ai_mode='auto'), admin, session)
    assert not first.get('conversation_id') and not first.get('draft_id')
    draft = await chat(ChatIn(message='5 juta', unit_usaha_id=unit.id, ai_mode='auto', ai_conversation_id=first['ai_conversation_id']), admin, session)
    assert 'Suryaman' in mocked.call_args.args[2][-1]['content']
    assert draft['can_revise'] and not await session.scalar(select(YieldPartner).where(YieldPartner.name == 'Suryaman'))
    revision = await chat(ChatIn(message='modalnya jadi 6 juta', unit_usaha_id=unit.id, ai_mode='auto', ai_conversation_id=draft['ai_conversation_id'], revision_draft_id=draft['draft_id']), admin, session)
    assert revision['draft_id'] != draft['draft_id']
    assert (await session.get(RiskaDraft, revision['draft_id'])).payload['data']['name'] == 'Suryaman'
    with pytest.raises(HTTPException) as error:
        await execute(draft['draft_id'], admin, session)
    assert error.value.status_code == 409
    await execute(revision['draft_id'], admin, session)
    partner = await session.scalar(select(YieldPartner).where(YieldPartner.name == 'Suryaman'))
    assert partner.capital == Decimal('6000000')


@pytest.mark.asyncio
async def test_manager_tool_cannot_escape_unit(context, monkeypatch):
    session, _, manager, _, _, air, *_ = context
    model(monkeypatch, [tool('baca_laporan', {'report': 'profit_loss', 'scope': 'units', 'unit_ids': [air.id]})])
    with pytest.raises(HTTPException) as error:
        await chat(ChatIn(message='bandingkan unit lain', ai_mode='auto', session_id='manager-session'), manager, session)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_provider_failure_after_report_keeps_official_rows(context, monkeypatch):
    session, admin, *_ = context
    model(monkeypatch, [tool('baca_laporan', {'report': 'profit_loss', 'scope': 'all_units', 'year': 2026}), HTTPException(429, 'Kuota habis')])
    result = await chat(ChatIn(message='laporan tahun 2026', ai_mode='auto'), admin, session)
    assert result['rows'] and 'Kuota habis' in result['message']


@pytest.mark.asyncio
async def test_revocation_after_model_response_prevents_tool(context, monkeypatch):
    session, admin, manager, _, toko, *_ = context
    async def revoked(*args):
        manager.active = False
        await session.flush()
        return tool('data_resmi', {'resource': 'produk', 'action': 'delete', 'entity': toko.id, 'target': 'SKU-012'})
    monkeypatch.setattr(riska_agent_provider, 'call', revoked)
    with pytest.raises(HTTPException) as error:
        await chat(ChatIn(message='hapus produk', ai_mode='sol', session_id='manager-session'), manager, session)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_structured_update_reuses_official_validation(context, monkeypatch):
    session, admin, _, _, toko, _, product, *_ = context
    model(monkeypatch, [tool('data_resmi', {'resource': 'produk', 'action': 'update', 'entity': toko.id, 'target': product.id, 'fields': {'sell_price': '45 ribu'}})])
    result = await chat(ChatIn(message='harga jual daster jadi 45 ribu', unit_usaha_id=toko.id, ai_mode='auto'), admin, session)
    assert result.get('draft_id'), result
    await execute(result['draft_id'], admin, session)
    await session.refresh(product)
    assert product.sell_price == Decimal('45000')


@pytest.mark.asyncio
async def test_retried_operation_does_not_call_model_again(context, monkeypatch):
    from uuid import uuid4
    session, admin, *_ = context
    mocked = model(monkeypatch, [answer('Halo, ada yang ingin dibantu?')])
    body = ChatIn(message='Halo', ai_mode='auto', operation_id=uuid4())
    first = await chat(body, admin, session)
    retry = await chat(body, admin, session)
    assert first['message'] == retry['message'] and mocked.await_count == 1
    with pytest.raises(HTTPException) as error:
        await chat(body.model_copy(update={'message': 'Pesan lain'}), admin, session)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_assignment_is_only_a_draft_until_confirmed(context, monkeypatch):
    from modules.siabumdes.identity.infrastructure.models import RiskaAssignment
    session, admin, manager, *_ = context
    model(monkeypatch, [tool('draf_penugasan', {'recipient': manager.username, 'allowed': False})])
    result = await chat(ChatIn(message='akhiri bantuan manager', ai_mode='auto'), admin, session)
    assert result.get('draft_id') and (await session.get(RiskaAssignment, manager.id)).active
    await execute(result['draft_id'], admin, session)
    assert not (await session.get(RiskaAssignment, manager.id)).active


@pytest.mark.asyncio
async def test_direct_report_uses_no_model_and_is_available_for_followup(context, monkeypatch):
    session, admin, _, _, toko, *_ = context
    mocked = model(monkeypatch, [answer('Penjelasan berdasarkan laporan resmi sebelumnya.')])
    direct = await chat(ChatIn(message='laba rugi tahun 2026', unit_usaha_id=toko.id, ai_mode='internal'), admin, session)
    assert direct.get('rows') and mocked.await_count == 0
    await chat(ChatIn(message='jelaskan biayanya', unit_usaha_id=toko.id, ai_mode='auto', ai_conversation_id=direct['ai_conversation_id']), admin, session)
    envelope = json.loads(mocked.call_args.args[2][-1]['content'])
    assert envelope['konteks']['laporan_terakhir']['rows']
