"""Atomic payment/finance linkage using existing period guards and journals."""
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from modules.siabumdes.adapters.api.scope import assert_can_mutate_period, assert_not_readonly
from modules.siabumdes.application.transaction_proofs import delete_transaction_proofs
from modules.siabumdes.application.transaction_journal import sync_transaction_journal
from modules.siabumdes.infrastructure.models import Account, JournalEntry, Transaction, TransactionType, YieldPartner, YieldPayment

TYPE_CODE = "pendapatan_bagi_hasil_unit4"


async def ensure_yield_type(session):
    # Seed only when both canonical UU04 accounts exist; preserve live configuration.
    accounts = (await session.execute(select(Account.code).where(Account.group_code == 'UU04', Account.code.in_(['1.1.01.14', '4.1.01.24'])))).scalars().all()
    if len(accounts) == 2:
        await session.execute(insert(TransactionType).values(code=TYPE_CODE, name='Pendapatan Bagi Hasil Unit 4',
            debit='1.1.01.14', credit='4.1.01.24', group_code='UU04', unit_codes=['UU04'])
            .on_conflict_do_nothing(constraint='uq_tx_types_code_group'))


async def lock_partner(session, partner_id, unit):
    partner = (await session.execute(select(YieldPartner).where(YieldPartner.id == partner_id,
        YieldPartner.unit_usaha_id == unit.id).with_for_update())).scalar_one_or_none()
    if not partner:
        raise HTTPException(404, 'Mitra tidak ditemukan')
    return partner


def week_of(day: date) -> int:
    """Minggu ke-N dalam bulan: minggu ke-1 = tgl 1 s.d. Sabtu pertama, lalu Minggu–Sabtu (maks. 5)."""
    first_saturday = 1 + (5 - day.replace(day=1).weekday()) % 7
    if day.day <= first_saturday:
        return 1
    return min(5, 1 + -(-(day.day - first_saturday) // 7))


def effective_week(payment) -> int:
    """Data bulanan lama (week 0) ditampilkan di minggu tanggal transaksinya (default minggu ke-1)."""
    if payment.week:
        return payment.week
    return week_of(payment.transaction_date) if payment.transaction_date else 1


async def find_payment(session, partner, year, month, week=None):
    """week=None: pembayaran apa pun di bulan itu (perilaku lama, dipakai Riska).
    week=1..5: isian minggu tsb, termasuk data lama (week 0) yang jatuh di minggu itu."""
    rows = [r for r in (await session.execute(select(YieldPayment).where(YieldPayment.partner_id == partner.id,
        YieldPayment.year == year, YieldPayment.month == month).order_by(YieldPayment.week.asc()))).scalars()
        if r.partner_id == partner.id and r.month == month]
    if week is None or week == 0:
        return next((r for r in rows if r.week == 0), None) if week == 0 else (rows[0] if rows else None)
    return next((r for r in rows if r.week == week), None) or next(
        (r for r in rows if r.week == 0 and effective_week(r) == week), None)


async def save_linked_payment(session, actor, unit, partner, body, amount):
    assert_not_readonly(actor)
    if amount <= 0:
        raise HTTPException(422, 'Nominal pembayaran harus lebih dari nol untuk membuat transaksi')
    week = getattr(body, "week", 0) or 0
    payment = await find_payment(session, partner, body.year, body.month, week)
    tx = await session.get(Transaction, payment.transaction_id, with_for_update=True) if payment and payment.transaction_id else None
    if payment and payment.transaction_id and not tx:
        raise HTTPException(409, 'Transaksi terkait tidak ditemukan; hubungi admin')
    if tx:
        await assert_can_mutate_period(session, actor, tx.date, unit.id)
    await assert_can_mutate_period(session, actor, body.transaction_date, unit.id)
    kind = await session.scalar(select(TransactionType).where(TransactionType.code == TYPE_CODE, TransactionType.group_code == unit.code))
    if not kind or not kind.debit or not kind.credit:
        raise HTTPException(422, f'Konfigurasikan jenis {TYPE_CODE} beserta akun debit/kredit di COA UU04')
    credit = await session.scalar(select(Account).where(Account.code == kind.credit, Account.group_code == unit.code))
    if not credit or credit.category != 'pendapatan':
        raise HTTPException(422, 'Akun kredit jenis transaksi Imbal Hasil harus akun pendapatan UU04')
    tx = tx or Transaction(unit_usaha_id=unit.id, created_by=actor.id, proofs=[])
    tx.date, tx.amount = body.transaction_date, amount
    tx.transaction_type = TYPE_CODE
    if week:
        tx.description = f'Pembayaran Imbal Hasil - {partner.name} - Minggu {week} - {body.month:02d}/{body.year}'
    else:
        tx.description = f'Pembayaran Imbal Hasil - {partner.name} - {body.month:02d}/{body.year}'
    tx.debit_account_code, tx.credit_account_code = kind.debit, kind.credit
    tx.reference = f'YIELD:{partner.id}:{body.year}:{body.month:02d}' + (f':W{week}' if week else '')
    session.add(tx)
    await session.flush()
    await sync_transaction_journal(session, tx, unit.code)
    payment = payment or YieldPayment(partner_id=partner.id, year=body.year, month=body.month)
    payment.amount, payment.automatic = amount, body.automatic
    payment.week = week
    payment.transaction_id, payment.transaction_date = tx.id, tx.date
    session.add(payment)
    return payment


async def delete_linked_payment(session, actor, unit, partner, body):
    week = getattr(body, "week", 0) or 0
    payment = await find_payment(session, partner, body.year, body.month, week or None)
    if not payment:
        raise HTTPException(404, 'Belum ada pembayaran untuk bulan yang dipilih')
    assert_not_readonly(actor)
    tx = await session.get(Transaction, payment.transaction_id, with_for_update=True) if payment.transaction_id else None
    if payment.transaction_id and not tx:
        raise HTTPException(409, 'Transaksi terkait tidak ditemukan; hubungi admin')
    await assert_can_mutate_period(session, actor, tx.date if tx else (payment.transaction_date or body.transaction_date), unit.id)
    if tx:
        await delete_transaction_proofs(list(tx.proofs or []))
    await session.delete(payment)
    await session.flush()  # remove FK before deleting linked finance rows
    if tx:
        entry = await session.scalar(select(JournalEntry).where(JournalEntry.transaction_id == tx.id))
        if entry:
            await session.delete(entry)
            await session.flush()
        await session.delete(tx)
