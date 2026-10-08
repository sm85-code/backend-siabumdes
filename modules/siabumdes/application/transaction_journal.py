"""Shared journal synchronization for normal and linked automatic transactions."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from modules.siabumdes.infrastructure.models import Account, JournalEntry, Transaction
from modules.siabumdes.application.services import FinanceService

async def _account(session: AsyncSession, code: str, group: str) -> Account:
    row = (
        await session.execute(select(Account).where(Account.code == code, Account.group_code == group))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail=f"Akun {code} tidak ada di kelompok {group}")
    return row


async def sync_transaction_journal(session: AsyncSession, tx: Transaction, group: str) -> None:
    debit = await _account(session, tx.debit_account_code, group)
    credit = await _account(session, tx.credit_account_code, group)
    # Query explicitly instead of touching tx.journal_entry: for a just-flushed
    # (now-persistent) Transaction that relationship isn't guaranteed to be
    # loaded, and a plain attribute access would trigger a lazy load outside
    # an awaited context, raising MissingGreenlet under AsyncSession.
    existing_entry = await session.scalar(
        select(JournalEntry).where(JournalEntry.transaction_id == tx.id)
    )
    if existing_entry:
        await session.delete(existing_entry)
        await session.flush()
    await FinanceService(session).create_journal_entry(
        transaction_id=tx.id,
        entry_date=tx.date,
        memo=tx.description,
        debit_account_id=debit.id,
        credit_account_id=credit.id,
        amount=tx.amount,
    )
