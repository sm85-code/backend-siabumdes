"""Core finance HTTP API (/api/v1/siabumdes)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.application.services import FinanceService
from shared.database import get_db

router = APIRouter(prefix="/api/v1/siabumdes", tags=["siabumdes"])


class JournalEntryRequest(BaseModel):
    transaction_id: str
    entry_date: date
    memo: str = ""
    debit_account_id: str
    credit_account_id: str
    amount: Decimal = Field(gt=0)


@router.post("/journal-entry")
async def create_journal_entry(
    body: JournalEntryRequest,
    session: AsyncSession = Depends(get_db),
):
    svc = FinanceService(session)
    try:
        entry = await svc.create_journal_entry(
            transaction_id=body.transaction_id,
            entry_date=body.entry_date,
            memo=body.memo,
            debit_account_id=body.debit_account_id,
            credit_account_id=body.credit_account_id,
            amount=body.amount,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "id": entry.id,
        "transaction_id": entry.transaction_id,
        "entry_date": entry.entry_date.isoformat(),
        "memo": entry.memo,
    }


@router.get("/general-ledger")
async def general_ledger(
    account_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    svc = FinanceService(session)
    rows = await svc.get_general_ledger(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
    )
    return {"items": rows, "count": len(rows)}


@router.get("/balance-sheet")
async def balance_sheet(
    as_of: date = Query(...),
    group_code: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    svc = FinanceService(session)
    return await svc.calculate_balance_sheet(as_of=as_of, group_code=group_code)
