"""Optional bounded language adapter. Models never execute writes or generate SQL."""
import os
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import update, select, func
from sqlalchemy.dialects.postgresql import insert
from shared.database import SessionLocal
from modules.siabumdes.identity.infrastructure.models import RiskaAiBudget, RiskaAiCall

MODELS = {'sol': ('OPENAI_API_KEY', 'OPENAI_MODEL', 'gpt-6.1-sol'), 'sonnet': ('ANTHROPIC_API_KEY', 'RISKA_SONNET_MODEL', 'claude-sonnet-5-5')}
ALLOWED = {'sol': {'gpt-6.1-sol', 'gpt-6-luna'}, 'sonnet': {'claude-sonnet-5-5', 'claude-sonnet-4-6'}}
RESERVE = Decimal('0.06')


def ready(provider):
    return os.getenv('RISKA_AI_ENABLED', 'true').lower() == 'true' and bool(os.getenv(MODELS[provider][0], '').strip())


def budget_summary(calls, reserved, maximum, limit, now, statuses=None):
    remaining = max(0, min(limit - calls, int(max(Decimal('0'), maximum - reserved) / RESERVE)))
    reason = 'Batas jumlah panggilan tercapai.' if calls >= limit else 'Sisa anggaran tidak cukup untuk cadangan satu panggilan.' if remaining == 0 else None
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {'calls': calls, 'call_limit': limit, 'reserved_usd': str(reserved),
            'daily_limit_usd': str(maximum), 'per_call_usd': str(RESERVE),
            'remaining_calls': remaining, 'exhausted': remaining == 0, 'reason': reason,
            'resets_at': reset.isoformat(), 'statuses': statuses or {}}


async def budget_status(session):
    now = datetime.now(ZoneInfo('Asia/Jakarta'))
    try:
        maximum = Decimal(os.getenv('RISKA_AI_DAILY_USD', '1.20'))
        limit = int(os.getenv('RISKA_AI_DAILY_CALLS', '20'))
        if not maximum.is_finite() or maximum <= 0 or limit <= 0:
            raise ValueError()
    except (ValueError, ArithmeticError):
        return {'unavailable': True}
    budget = await session.get(RiskaAiBudget, now.date().isoformat())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    counts = dict((await session.execute(select(RiskaAiCall.status, func.count()).where(
        RiskaAiCall.created_at >= start, RiskaAiCall.created_at < start + timedelta(days=1)
    ).group_by(RiskaAiCall.status))).all())
    return budget_summary(budget.calls if budget else 0, budget.reserved_usd if budget else Decimal('0'), maximum, limit, now, counts)


async def reserve(user_id, model):
    day = datetime.now(ZoneInfo('Asia/Jakarta')).date().isoformat()
    try:
        maximum = Decimal(os.getenv('RISKA_AI_DAILY_USD', '1.20'))
        limit = int(os.getenv('RISKA_AI_DAILY_CALLS', '20'))
        if not maximum.is_finite() or maximum <= 0 or limit <= 0:
            raise ValueError()
    except (ValueError, ArithmeticError):
        raise HTTPException(503, 'Pengaturan batas biaya Riska belum valid.')
    async with SessionLocal() as session:
        await session.execute(insert(RiskaAiBudget).values(day=day, calls=0, reserved_usd=0).on_conflict_do_nothing(index_elements=['day']))
        claimed = await session.scalar(update(RiskaAiBudget).where(RiskaAiBudget.day == day, RiskaAiBudget.calls < limit, RiskaAiBudget.reserved_usd + RESERVE <= maximum).values(calls=RiskaAiBudget.calls+1, reserved_usd=RiskaAiBudget.reserved_usd+RESERVE).returning(RiskaAiBudget.day))
        if not claimed:
            raise HTTPException(429, 'Batas bantuan AI bersama hari ini tercapai. Setiap panggilan mencadangkan USD 0,06, termasuk panggilan gagal; ini bukan tagihan aktual. Kuota direset pukul 00.00 WIB. Pilih mode Internal untuk melanjutkan tanpa biaya AI.')
        call = RiskaAiCall(user_id=user_id, model=model)
        session.add(call)
        await session.commit()
        return call.id


async def record(ident, status, incoming=0, outgoing=0):
    async with SessionLocal() as session:
        await session.execute(update(RiskaAiCall).where(RiskaAiCall.id == ident).values(status=status, input_tokens=incoming, output_tokens=outgoing))
        await session.commit()
