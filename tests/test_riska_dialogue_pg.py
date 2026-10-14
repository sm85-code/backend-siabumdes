"""Real PostgreSQL regressions for clarification, Admin snapshots, ownership and replay."""
import os
import uuid
from datetime import date
from decimal import Decimal
import pytest
import pytest_asyncio

if not os.getenv('RISKA_TEST_DATABASE_URL'):
    pytest.skip('Requires isolated PostgreSQL integration configuration', allow_module_level=True)

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select, func, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: E402
from shared.database import Base  # noqa: E402
from modules.siabumdes.adapters.api.v1.riska import chat, ChatIn, comparison, ComparisonIn  # noqa: E402
from modules.siabumdes.adapters.api.v1.riska_execution import execute, cancel  # noqa: E402
from modules.siabumdes.identity.infrastructure.models import User, AiSessionPermission, RiskaAssignment, LockedPeriod, ClosedPeriod, RiskaDraft  # noqa: E402
from modules.siabumdes.infrastructure.models import Account, UnitUsaha, Transaction  # noqa: E402
from modules.siabumdes.inventory.infrastructure.models import Product, StockCategory, Vendor  # noqa: E402
from modules.siabumdes.coa_taxonomy import SUB_IKHTISAR_LR, SUB_SALDO_LABA  # noqa: E402
from modules.siabumdes.application.closing import SUB_UTANG_BH_UNIT  # noqa: E402
from modules.siabumdes.application.bagi_hasil_transfer import KAS_PUSAT_CODE, PENDAPATAN_BH_PUSAT_CODE  # noqa: E402


@pytest_asyncio.fixture
async def context():
    schema = 'riska_dialogue_' + uuid.uuid4().hex
    engine = create_async_engine(os.environ['RISKA_TEST_DATABASE_URL'])
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA {schema}'))
        async with scoped.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(scoped, expire_on_commit=False, autoflush=False)() as session:
            toko = UnitUsaha(code='UU05', name='Unit Toko Desa', business_type='perdagangan', active=True)
            air = UnitUsaha(code='UU04', name='Unit Air Desa', active=True)
            session.add_all([toko, air])
            await session.flush()
            admin = User(username='admin', name='Admin', email='admin@test.local', role='admin', password_hash='x')
            manager = User(username='manager', name='Manager', email='manager@test.local', role='pengelola', password_hash='x', unit_usaha_id=toko.id)
            other = User(username='other', name='Other', email='other@test.local', role='admin', password_hash='x')
            session.add_all([admin, manager, other, AiSessionPermission(unit_usaha_id=toko.id, is_allowed=True)])
            category = StockCategory(code='test', name='Test', unit_usaha_id=toko.id)
            vendor = Vendor(name='Toko Makmur', unit_usaha_id=toko.id)
            session.add_all([category, vendor])
            await session.flush()
            session.add(RiskaAssignment(user_id=manager.id, session_id='manager-session', assigned_by=admin.id,
                role='pengelola', unit_usaha_id=toko.id, active=True))
            product = Product(sku='SKU-012', name='Daster Arab Jumbo', category_id=category.id, unit_usaha_id=toko.id,
                              cost_price=Decimal('20000'), qty_on_hand=10)
            session.add(product)
            for unit in [toko, air]:
                suffix = unit.code[-1]
                for code, category, sub, normal in [(f'1.1.01.1{suffix}', 'aset', 'kas_bank', 'debit'),
                        (f'4.1.01.1{suffix}', 'pendapatan', 'pendapatan_operasional', 'kredit'),
                        (f'1.1.05.1{suffix}', 'aset', 'aset_lancar', 'debit'),
                        ('9100', 'ekuitas', SUB_IKHTISAR_LR, 'kredit'), ('9200', 'ekuitas', SUB_SALDO_LABA, 'kredit'),
                        ('9300', 'kewajiban', SUB_UTANG_BH_UNIT, 'kredit')]:
                    session.add(Account(code=code, name=code, category=category, subcategory=sub, normal_balance=normal,
                                        group_code=unit.code, unit_usaha_id=unit.id))
                session.add(Transaction(date=date(2026, 9, 15), amount=Decimal('50000'), transaction_type='income',
                    debit_account_code=f'1.1.01.1{suffix}', credit_account_code=f'4.1.01.1{suffix}',
                    unit_usaha_id=unit.id, description='Income', created_by=admin.id))
            for code, cat, normal in [(KAS_PUSAT_CODE, 'aset', 'debit'), (PENDAPATAN_BH_PUSAT_CODE, 'pendapatan', 'kredit')]:
                session.add(Account(code=code, name=code, category=cat, subcategory='kas_bank' if cat == 'aset' else 'pendapatan_operasional',
                                    normal_balance=normal, group_code='BUMDES'))
            await session.flush()
            yield session, admin, manager, other, toko, air, product, vendor
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_clarification_and_natural_stock_post_once(context):
    session, admin, manager, other, unit, _, product, _ = context
    count = await session.scalar(select(func.count()).select_from(Transaction))
    reply = await chat(ChatIn(session_id='manager-session', message='Riska, input barang masuk buat SKU-012 sebanyak 5 pcs dari pemasok "Toko Makmur" hari ini'), manager, session)
    assert 'conversation_id' in reply and 'harga per barang' in reply['message']
    assert await session.scalar(select(func.count()).select_from(Transaction)) == count
    with pytest.raises(HTTPException):
        await chat(ChatIn(message='20 ribu', conversation_id=reply['conversation_id']), other, session)
    draft = await chat(ChatIn(session_id='manager-session', message='20 ribu', conversation_id=reply['conversation_id']), manager, session)
    assert draft['preview']['details']['harga'] == '20000'
    assert product.qty_on_hand == 10
    await execute(draft['draft_id'], manager, session)
    await session.refresh(product)
    assert product.qty_on_hand == 15
    with pytest.raises(HTTPException) as replay:
        await execute(draft['draft_id'], manager, session)
    assert replay.value.status_code == 409
    assert await session.scalar(select(func.count()).select_from(Transaction)) == count + 1
    assert (await session.get(RiskaDraft, reply['conversation_id'])).consumed


@pytest.mark.asyncio
async def test_admin_lock_preview_cancel_and_nonadmin_denial(context):
    session, admin, manager, other, *_ = context
    result = await chat(ChatIn(message='Riska, lakukan kunci periode keuangan bulan ini'), admin, session)
    assert result['confirmation_level'] == 'admin'
    assert await session.scalar(select(func.count()).select_from(LockedPeriod)) == 0
    assert 'Akses ditolak' in (await chat(ChatIn(session_id='manager-session', message='tutup buku bulan ini'), manager, session))['message']
    with pytest.raises(HTTPException):
        await execute(result['draft_id'], other, session)
    await cancel(result['draft_id'], admin, session)
    assert await session.scalar(select(func.count()).select_from(LockedPeriod)) == 0
    result = await chat(ChatIn(message='kunci periode 2026-08'), admin, session)
    await execute(result['draft_id'], admin, session)
    assert await session.scalar(select(func.count()).select_from(LockedPeriod)) == 1
    with pytest.raises(HTTPException):
        await execute(result['draft_id'], admin, session)


@pytest.mark.asyncio
async def test_admin_close_and_transfer_use_existing_accounting_workflow(context):
    session, admin, _, _, unit, *_ = context
    count = await session.scalar(select(func.count()).select_from(Transaction))
    reply = await chat(ChatIn(message='tutup buku unit toko September 2026'), admin, session)
    assert await session.scalar(select(func.count()).select_from(ClosedPeriod)) == 0
    assert await session.scalar(select(func.count()).select_from(Transaction)) == count
    await execute(reply['draft_id'], admin, session)
    assert await session.scalar(select(func.count()).select_from(ClosedPeriod)) == 1
    count = await session.scalar(select(func.count()).select_from(Transaction))
    reply = await chat(ChatIn(message='transfer bagi hasil unit toko September 2026'), admin, session)
    assert await session.scalar(select(func.count()).select_from(Transaction)) == count
    await execute(reply['draft_id'], admin, session)
    txs = list((await session.scalars(select(Transaction).where(Transaction.reference == 'BAGIHASIL-2026-09-UU05'))).all())
    assert len(txs) == 3
    assert sum(t.amount for t in txs if t.unit_usaha_id is None) == Decimal('35000')
    assert sum(t.amount for t in txs if t.unit_usaha_id == unit.id) == Decimal('50000')


@pytest.mark.asyncio
async def test_admin_comparison_history_and_scoped_denial(context):
    session, admin, manager, _, toko, air, *_ = context
    reply = await chat(ChatIn(message='Berapa total laba rugi gabungan unit toko dan unit air September 2026?'), admin, session)
    assert len(reply['rows']) == 3
    assert reply['rows'][-1]['laba_bersih'] == '100000.00'
    assert 'tanpa eliminasi' in reply['message']
    same = await comparison(ComparisonIn(unit_ids=[toko.id, air.id], start_date=date(2026, 9, 1), end_date=date(2026, 9, 30)), admin, session)
    assert same['rows'][-1]['laba_bersih'] == '100000.00'
    with pytest.raises(HTTPException) as denied:
        await chat(ChatIn(session_id='manager-session', message='laba rugi gabungan unit toko dan unit air'), manager, session)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_changed_data_and_revoked_permission_block_confirmation(context):
    session, admin, manager, _, unit, _, product, _ = context
    reply = await chat(ChatIn(message='tutup buku unit toko September 2026'), admin, session)
    session.add(Transaction(date=date(2026, 9, 20), amount=Decimal('1000'), transaction_type='income', unit_usaha_id=unit.id,
                           debit_account_code='1.1.01.15', credit_account_code='4.1.01.15', description='New data', created_by=admin.id))
    await session.flush()
    with pytest.raises(HTTPException) as stale:
        await execute(reply['draft_id'], admin, session)
    assert stale.value.status_code == 409
    assert await session.scalar(select(func.count()).select_from(ClosedPeriod)) == 0
    reply = await chat(ChatIn(session_id='manager-session', message='barang masuk SKU-012 sebanyak 1 pcs harga 20 ribu dari pemasok "Toko Makmur" hari ini'), manager, session)
    permission = await session.get(RiskaAssignment, manager.id)
    permission.active = False
    await session.flush()
    with pytest.raises(HTTPException) as revoked:
        await execute(reply['draft_id'], manager, session)
    assert revoked.value.status_code == 403
    assert product.qty_on_hand == 10


@pytest.mark.asyncio
async def test_user_assignment_ends_and_old_draft_never_revives(context):
    from modules.siabumdes.application.riska_sessions import set_assignment, finish_assignment
    session, admin, manager, _, unit, *_ = context
    reply = await chat(ChatIn(session_id='manager-session', message='barang masuk SKU-012 sebanyak 1 pcs harga 20 ribu dari pemasok "Toko Makmur" hari ini'), manager, session)
    await finish_assignment(session, manager, 'manager-session')
    with pytest.raises(HTTPException) as disabled:
        await chat(ChatIn(session_id='manager-session', message='saldo kas'), manager, session)
    assert disabled.value.status_code == 403
    grant = await set_assignment(session, admin, manager.id, True)
    assert grant['session_id'] != 'manager-session'
    with pytest.raises(HTTPException):
        await execute(reply['draft_id'], manager, session)
    with pytest.raises(HTTPException) as stale:
        await chat(ChatIn(session_id='manager-session', message='saldo kas'), manager, session)
    assert stale.value.status_code == 409
    await chat(ChatIn(session_id=grant['session_id'], message='saldo kas'), manager, session)
    manager.role = 'pengawas'
    await session.flush()
    with pytest.raises(HTTPException):
        await chat(ChatIn(session_id=grant['session_id'], message='saldo kas'), manager, session)


@pytest.mark.asyncio
async def test_admin_delegation_chat_and_recipient_rights(context):
    session, admin, manager, _, unit, *_ = context
    from modules.siabumdes.application.riska_sessions import finish_assignment
    await finish_assignment(session, manager, 'manager-session')
    rejected = await chat(ChatIn(message='jangan aktifkan dirimu di pengelola 5'), admin, session)
    assert 'draft_id' not in rejected
    reply = await chat(ChatIn(message='aktifkan dirimu di pengelola 5'), admin, session)
    assert reply['preview']['unit'] == manager.name
    row = await session.get(RiskaAssignment, manager.id)
    assert not row.active
    await execute(reply['draft_id'], admin, session)
    assert row.active
    denied = await chat(ChatIn(session_id=row.session_id, message='transfer bagi hasil bulan ini'), manager, session)
    assert 'Akses ditolak' in denied['message']
    result = await chat(ChatIn(session_id=row.session_id, message='tidak, selesai'), manager, session)
    assert result['session_ended'] and not row.active


@pytest.mark.asyncio
async def test_delegation_does_not_grant_readonly_role_write_access(context):
    from modules.siabumdes.application.riska_sessions import set_assignment
    session, admin, manager, _, unit, *_ = context
    manager.role = 'pengawas'
    await session.flush()
    grant = await set_assignment(session, admin, manager.id, True)
    result = await chat(ChatIn(session_id=grant['session_id'], message='laba rugi September 2026'), manager, session)
    assert result['rows']
    with pytest.raises(HTTPException) as denied:
        await chat(ChatIn(session_id=grant['session_id'], message='input barang masuk', unit_usaha_id=unit.id), manager, session)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_http_rbac_ignores_client_role_and_admin_only_assignments(context):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from shared.database import get_db
    from modules.siabumdes.adapters.api.deps import get_current_user
    from modules.siabumdes.adapters.api.v1.riska import router
    from modules.siabumdes.adapters.api.v1.riska_execution import router as execution_router, admin_router
    session, admin, manager, _, unit, *_ = context
    app = FastAPI()
    app.include_router(router)
    app.include_router(execution_router)
    app.include_router(admin_router)
    actor = manager

    async def identity():
        return actor

    async def database():
        yield session

    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for method in ['get', 'patch']:
            kwargs = {'json': {'user_id': manager.id, 'is_allowed': True}} if method == 'patch' else {}
            response = await getattr(client, method)('/api/v1/admin/riska-assignments', **kwargs)
            assert response.status_code == 403
        response = await client.post('/api/riska/chat', json={'message': 'tutup buku bulan ini',
                                     'session_id': 'manager-session', 'user_role': 'admin'})
        assert response.status_code == 200 and 'Akses ditolak' in response.json()['message']
        response = await client.post('/api/riska/chat', json={'message': 'saldo kas',
                                     'session_id': 'manager-session', 'unit_usaha_id': 'another-unit'})
        assert response.status_code == 403
        actor = admin
        response = await client.patch('/api/v1/admin/riska-assignments', json={'user_id': manager.id, 'is_allowed': False})
        assert response.status_code == 200 and response.json()['enabled'] is False
        actor = manager
        response = await client.get('/api/riska/availability')
        assert response.json()['enabled'] is False


@pytest.mark.asyncio
async def test_simple_no_finishes_only_after_offer_of_more_help(context):
    session, _, manager, _, _, *_ = context
    reply = await chat(ChatIn(session_id='manager-session', message='input barang masuk SKU-012 hari ini'), manager, session)
    assert reply['conversation_id']
    result = await chat(ChatIn(session_id='manager-session', message='tidak', conversation_id=reply['conversation_id']), manager, session)
    assert not result.get('session_ended')
    await chat(ChatIn(session_id='manager-session', message='saldo kas'), manager, session)
    result = await chat(ChatIn(session_id='manager-session', message='tidak'), manager, session)
    assert result['session_ended']
