"""Core finance application services (async SQLAlchemy)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from modules.siabumdes.infrastructure.models import (
    Account,
    JournalEntry,
    JournalItem,
)


class FinanceService:
    """Journal, ledger, and statement operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_journal_entry(
        self,
        *,
        transaction_id: str,
        entry_date: date,
        memo: str,
        debit_account_id: str,
        credit_account_id: str,
        amount: Decimal,
    ) -> JournalEntry:
        """Create balanced double-entry journal (debit + credit lines)."""
        if amount <= 0:
            raise ValueError("amount must be > 0")

        entry = JournalEntry(
            transaction_id=transaction_id,
            entry_date=entry_date,
            memo=memo,
        )
        entry.items = [
            JournalItem(account_id=debit_account_id, side="debit", amount=amount),
            JournalItem(account_id=credit_account_id, side="credit", amount=amount),
        ]
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_general_ledger(
        self,
        *,
        account_id: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        """Return ledger rows ordered by entry date."""
        stmt = (
            select(JournalItem)
            .join(JournalEntry)
            .options(selectinload(JournalItem.journal_entry), selectinload(JournalItem.account))
            .order_by(JournalEntry.entry_date, JournalItem.id)
        )
        if account_id:
            stmt = stmt.where(JournalItem.account_id == account_id)
        if start_date:
            stmt = stmt.where(JournalEntry.entry_date >= start_date)
        if end_date:
            stmt = stmt.where(JournalEntry.entry_date <= end_date)

        rows = (await self.session.scalars(stmt)).all()
        result: list[dict[str, Any]] = []
        for item in rows:
            result.append(
                {
                    "date": item.journal_entry.entry_date.isoformat(),
                    "account_id": item.account_id,
                    "account_code": item.account.code if item.account else None,
                    "side": item.side,
                    "amount": str(item.amount),
                    "memo": item.journal_entry.memo,
                    "journal_entry_id": item.journal_entry_id,
                }
            )
        return result

    async def calculate_balance_sheet(
        self,
        *,
        as_of: date,
        group_code: Optional[str] = None,
    ) -> dict[str, Any]:
        """Aggregate asset / liability / equity balances as of date (stub-ready)."""
        stmt = (
            select(JournalItem, Account, JournalEntry)
            .join(Account, JournalItem.account_id == Account.id)
            .join(JournalEntry, JournalItem.journal_entry_id == JournalEntry.id)
            .where(JournalEntry.entry_date <= as_of)
        )
        if group_code:
            stmt = stmt.where(Account.group_code == group_code)

        balances: dict[str, Decimal] = {}
        categories: dict[str, str] = {}

        for item, account, _entry in (await self.session.execute(stmt)).all():
            sign = Decimal("1") if item.side == account.normal_balance else Decimal("-1")
            # normal_balance is debit|kredit; side is debit|credit
            nb = "debit" if account.normal_balance == "debit" else "credit"
            side = item.side
            delta = item.amount if side == nb else -item.amount
            balances[account.code] = balances.get(account.code, Decimal("0")) + delta
            categories[account.code] = account.category

        sheet = {"aset": {}, "kewajiban": {}, "ekuitas": {}, "as_of": as_of.isoformat()}
        for code, bal in balances.items():
            cat = categories.get(code, "")
            if cat in sheet:
                sheet[cat][code] = str(bal)
        return sheet
