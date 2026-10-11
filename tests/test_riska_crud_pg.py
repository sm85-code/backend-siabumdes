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
