from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import hashlib
import os

if not (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")):
    os.environ["DATABASE_URL"] = "postgresql://placeholder@localhost/placeholder"

import httpx
import pytest
from fastapi import FastAPI

from modules.siabumdes.adapters.api.deps import get_current_user
from modules.siabumdes.adapters.api.v1.supplier_catalog_router import router
from modules.siabumdes.identity.infrastructure.models import User
from shared.database import get_db


async def setup_client(role="admin"):
    unit = SimpleNamespace(id="u5", name="Unit 5")
    owner = SimpleNamespace(id="admin", role=role, active=True)
    db = MagicMock()
    db.scalar = AsyncMock(return_value=unit)
    db.get = AsyncMock(side_effect=lambda model, key: owner if model is User else unit)
    db.commit = AsyncMock()
    db.delete = AsyncMock()
    app = FastAPI()
    app.include_router(router)

    async def user():
        return owner

    async def session():
        yield db

    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_db] = session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), db


@pytest.mark.asyncio
async def test_catalog_access_minted_only_by_admin_and_stored_as_hash():
    http, db = await setup_client()
    async with http:
        response = await http.post("/api/v1/uu05_inventory/supplier-catalog/access", json={})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    row = db.add.call_args.args[0]
    assert row.token_digest == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_digest and row.unit_usaha_id == "u5"
    http, db = await setup_client("pengelola")
    async with http:
        response = await http.post("/api/v1/uu05_inventory/supplier-catalog/access", json={})
    assert response.status_code == 403
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_returns_only_unit_products_retail_price_and_stock_without_writes():
    http, db = await setup_client()
    db.scalar.return_value = SimpleNamespace(issued_by="admin", unit_usaha_id="u5", expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    product = SimpleNamespace(id="p1", sku="KRS.FUR-1", name="Rak", unit_of_measure="pcs", sell_price=Decimal("150000"), qty_on_hand=7)
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: [(product, "Furniture")]))
    async with http:
        response = await http.get("/api/v1/uu05_inventory/supplier-catalog", headers={"X-ERP-Catalog-Key": "valid"})
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["sell_price"] == "150000" and item["qty_on_hand"] == 7
    assert set(item) == {"id", "sku", "name", "category_name", "unit_of_measure", "sell_price", "qty_on_hand"}
    assert "unit_usaha_id" in str(db.execute.call_args.args[0])
    db.commit.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_missing_or_expired_catalog_credentials_cannot_read_stock():
    http, db = await setup_client()
    db.scalar.return_value = SimpleNamespace(issued_by="admin", unit_usaha_id="u5", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    db.execute = AsyncMock()
    async with http:
        assert (await http.get("/api/v1/uu05_inventory/supplier-catalog")).status_code == 401
        assert (await http.get("/api/v1/uu05_inventory/supplier-catalog", headers={"X-ERP-Catalog-Key": "expired"})).status_code == 401
    db.execute.assert_not_called()
