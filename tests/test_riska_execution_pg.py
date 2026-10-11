"""Verify draft ownership, revocation and replay against actual PostgreSQL locks/postings."""
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest

if not os.getenv("RISKA_TEST_DATABASE_URL"):
    pytest.skip("Requires isolated PostgreSQL integration configuration", allow_module_level=True)

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: E402
from shared.database import Base  # noqa: E402
from modules.siabumdes.adapters.api.v1.riska_execution import draft, execute, DraftIn  # noqa: E402
from modules.siabumdes.identity.infrastructure.models import AiSessionPermission, User  # noqa: E402
from modules.siabumdes.infrastructure.models import Account, Transaction, TransactionType, UnitUsaha, YieldPartner  # noqa: E402


@pytest.mark.asyncio
async def test_confirm_posts_once_and_revocation_blocks_execution():
    schema = 'riska_' + uuid.uuid4().hex
    engine = create_async_engine(os.environ['RISKA_TEST_DATABASE_URL'])
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA {schema}'))
        async with scoped.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(scoped, expire_on_commit=False)() as session:
            unit = UnitUsaha(code='UU04', name='Unit test', active=True)
            session.add(unit)
            await session.flush()
            user = User(username='test', email='test@example.test', name='Test', role='pengelola',
                        password_hash='unused', unit_usaha_id=unit.id)
            permission = AiSessionPermission(unit_usaha_id=unit.id, is_allowed=True)
            session.add_all([user, permission])
            for code, category, subcategory in [('cash', 'aset', 'kas_bank'), ('income', 'pendapatan', 'pendapatan_operasional')]:
                session.add(Account(code=code, group_code='UU04', unit_usaha_id=unit.id, name=code,
                                    category=category, subcategory=subcategory, normal_balance='debit'))
            partner = YieldPartner(name='Partner', capital=Decimal('10000'), unit_usaha_id=unit.id)
            session.add_all([partner, TransactionType(code='pendapatan_bagi_hasil_unit4', name='Yield',
                                                     debit='cash', credit='income', group_code='UU04')])
            await session.flush()
            message = f'imbal mitra={partner.id} tanggal={date.today()} bulan=10 tahun=2026 nominal=300'
            result = await draft(DraftIn(message=message), user, session)
            assert await session.scalar(select(func.count()).select_from(Transaction)) == 0
            permission.is_allowed = False
            await session.flush()
            with pytest.raises(HTTPException) as denied:
                await execute(result['draft_id'], user, session)
            assert denied.value.status_code == 403
            permission.is_allowed = True
            await session.flush()
            await execute(result['draft_id'], user, session)
            with pytest.raises(HTTPException) as replay:
                await execute(result['draft_id'], user, session)
            assert replay.value.status_code == 409
            assert await session.scalar(select(func.count()).select_from(Transaction)) == 1
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        await engine.dispose()
