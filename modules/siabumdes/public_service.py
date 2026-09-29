"""Public contract: inventory → core finance (no cross-table SQL from inventory)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.application.services import FinanceService
from modules.siabumdes.infrastructure.models import Account, JournalEntry, Transaction


class SiabumdesPublicService:
    """Stable inter-module API for UU05 inventory financial postings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._finance = FinanceService(session)

    async def record_inventory_journal(
        self,
        *,
        movement_date: date,
        unit_usaha_id: str,
        amount: Decimal,
        debit_account_code: str,
        credit_account_code: str,
        description: str,
        reference: str,
        created_by: str = "system-inventory",
        transaction_type: str = "inventory_sync",
    ) -> str:
        """Post inventory/COGS impact into core finance.

        Returns transaction id. Idempotent on ``reference``.
        """
        if amount <= 0:
            raise ValueError("amount must be > 0")

        existing = await self.session.scalar(
            select(Transaction).where(Transaction.reference == reference)
        )
        if existing:
            return existing.id

        debit_acc = await self.session.scalar(
            select(Account).where(
                Account.code == debit_account_code,
                Account.active.is_(True),
            )
        )
        credit_acc = await self.session.scalar(
            select(Account).where(
                Account.code == credit_account_code,
                Account.active.is_(True),
            )
        )
        if not debit_acc or not credit_acc:
            raise ValueError(
                f"Account not found: debit={debit_account_code} credit={credit_account_code}"
            )

        tx = Transaction(
            date=movement_date,
            unit_usaha_id=unit_usaha_id,
            transaction_type=transaction_type,
            description=description,
            amount=amount,
            debit_account_code=debit_account_code,
            credit_account_code=credit_account_code,
            reference=reference,
            created_by=created_by,
        )
        self.session.add(tx)
        await self.session.flush()

        await self._finance.create_journal_entry(
            transaction_id=tx.id,
            entry_date=movement_date,
            memo=description,
            debit_account_id=debit_acc.id,
            credit_account_id=credit_acc.id,
            amount=amount,
        )
        return tx.id

    async def cancel_inventory_journal(self, reference: str) -> int:
        """Remove finance postings by inventory reference. Returns rows deleted."""
        tx = await self.session.scalar(
            select(Transaction).where(Transaction.reference == reference)
        )
        if not tx:
            return 0
        # Delete journal entry (+ items via ORM cascade) before the transaction.
        # ORM delete of Transaction alone would SET NULL journal_entries.transaction_id,
        # which violates NOT NULL and caused HTTP 500 on cancel-movement.
        entry = await self.session.scalar(
            select(JournalEntry).where(JournalEntry.transaction_id == tx.id)
        )
        if entry:
            await self.session.delete(entry)
            await self.session.flush()
        await self.session.delete(tx)
        await self.session.flush()
        return 1
