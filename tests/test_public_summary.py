"""Public summary executes its real ORM query against an isolated SQLite DB."""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1:5432/siabumdes_test")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, MetaData, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from modules.siabumdes.adapters.api.v1.public_router import router
from modules.siabumdes.infrastructure.models import Account, Transaction, UnitUsaha
from shared.database import get_db


@pytest.fixture
def public_db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    # Only the tables consumed by this endpoint; adapt PostgreSQL JSONB for SQLite.
    metadata = MetaData()
    for model in (UnitUsaha, Account, Transaction):
        model.__table__.to_metadata(metadata)
    metadata.tables["transactions"].c.proofs.type = JSON()
    metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            UnitUsaha(id="hq", code="BUMDES", name="Pusat"),
            UnitUsaha(id="shop", code="UU01", name="Toko"),
            UnitUsaha(id="lower", code="bumdes", name="Different code"),
        ])
        for code, category in [("cash", "aset"), ("income", "pendapatan"), ("expense", "beban"), ("cost", "hpp")]:
            session.add(Account(code=code, name=code, category=category, normal_balance="debit"))
        session.commit()

        class EndpointSession:
            async def execute(self, statement):
                return session.execute(statement)

        async def override_db():
            yield EndpointSession()

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = override_db
        with TestClient(app) as client:
            yield session, client
    engine.dispose()


def add_tx(session, unit_id, when, amount, debit="cash", credit="income"):
    session.add(Transaction(
        unit_usaha_id=unit_id, date=when, amount=Decimal(str(amount)),
        debit_account_code=debit, credit_account_code=credit,
        transaction_type="test", created_by="test",
    ))


def expected_summary(year, income="0.00", expense="0.00", profit="0.00", pades="0.00"):
    return {
        "year": year, "total_pendapatan": income, "total_beban": expense,
        "laba_bersih": profit, "pades_estimasi": pades,
        "trend": [
            {"month": f"{year}-{month:02d}", "pendapatan": "0.00", "beban": "0.00"}
            for month in range(1, 13)
        ],
    }


def test_public_summary_excludes_other_units_from_totals_and_every_month(public_db):
    session, client = public_db
    year = date.today().year
    add_tx(session, None, date(year, 1, 1), 1000)
    add_tx(session, "hq", date(year, 12, 31), 500)
    add_tx(session, None, date(year, 1, 15), 100, "expense", "cash")
    add_tx(session, "hq", date(year, 12, 15), 200, "cost", "cash")
    for month in range(1, 13):
        for unit_id in ("shop", "lower", "missing-unit"):
            add_tx(session, unit_id, date(year, month, 10), 99999)
            add_tx(session, unit_id, date(year, month, 11), 88888, "expense", "cash")
            add_tx(session, unit_id, date(year, month, 12), 77777, "cost", "cash")
    add_tx(session, None, date(year - 1, 12, 31), 99999)
    add_tx(session, "hq", date(year + 1, 1, 1), 99999, "expense", "cash")
    session.commit()

    expected = expected_summary(year, "1500.00", "300.00", "1200.00", "360.00")
    expected["trend"][0].update(pendapatan="1000.00", beban="100.00")
    expected["trend"][11].update(pendapatan="500.00", beban="200.00")
    response = client.get("/api/public/summary")
    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.parametrize("other_units_only", [False, True])
def test_public_summary_returns_zero_when_no_bumdes_transactions(public_db, other_units_only):
    session, client = public_db
    year = date.today().year
    if other_units_only:
        add_tx(session, "shop", date(year, 2, 1), 9000)
        add_tx(session, "shop", date(year, 3, 1), 5000, "cost", "cash")
        session.commit()
    response = client.get("/api/public/summary")
    assert response.status_code == 200
    assert response.json() == expected_summary(year)
