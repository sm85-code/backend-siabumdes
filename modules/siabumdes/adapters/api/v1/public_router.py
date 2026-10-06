"""Unauthenticated endpoints consumed by frontend-siabumdes Landing.jsx."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.application.reporting import ReportingService, _f
from modules.siabumdes.money_json import stringify_money_fields
from modules.siabumdes.infrastructure.models import Account, Transaction, UnitUsaha
from shared.database import get_db

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/summary")
async def public_summary(session: AsyncSession = Depends(get_db)):
    year = date.today().year
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    svc = ReportingService(session)

    accounts = {a.code: a for a in (await session.execute(select(Account))).scalars()}
    txs = list(
        (
            await session.execute(
                select(Transaction).where(
                    Transaction.date >= start,
                    Transaction.date <= end,
                    # BUMDES pusat has no unit ID (see unit_code_for).
                    or_(
                        Transaction.unit_usaha_id.is_(None),
                        Transaction.unit_usaha.has(UnitUsaha.code == "BUMDES"),
                    ),
                )
            )
        ).scalars()
    )

    total_p = 0.0
    total_b = 0.0
    monthly: dict[str, dict] = {}
    for m in range(1, 13):
        key = f"{year}-{m:02d}"
        monthly[key] = {"month": key, "pendapatan": 0.0, "beban": 0.0}

    for tx in txs:
        key = tx.date.isoformat()[:7]
        bucket = monthly.setdefault(key, {"month": key, "pendapatan": 0.0, "beban": 0.0})
        credit = accounts.get(tx.credit_account_code)
        debit = accounts.get(tx.debit_account_code)
        amt = _f(tx.amount)
        if credit and credit.category == "pendapatan":
            bucket["pendapatan"] += amt
            total_p += amt
        if debit and debit.category in {"beban", "hpp"}:
            bucket["beban"] += amt
            total_b += amt

    laba = total_p - total_b
    return stringify_money_fields({
        "year": year,
        "total_pendapatan": total_p,
        "total_beban": total_b,
        "laba_bersih": laba,
        "pades_estimasi": round(laba * 0.30),
        "trend": [monthly[f"{year}-{m:02d}"] for m in range(1, 13)],
    })
