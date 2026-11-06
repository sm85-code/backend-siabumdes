"""Disabled Riska rejects every API before auth, SQL or provider activity."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder@localhost/placeholder")

import httpx
import pytest
from fastapi import FastAPI

from modules.siabumdes.adapters.api.v1.riska import router
from modules.siabumdes.adapters.api.v1.riska_execution import router as execution_router, admin_router
from shared.database import get_db
from shared.features import require_riska_enabled, riska_enabled


@pytest.mark.parametrize("value", [None, "", "false", "0", "yes", "tru"])
def test_switch_fails_closed(monkeypatch, value):
    monkeypatch.delenv("RISKA_ENABLED", raising=False)
    if value is not None:
        monkeypatch.setenv("RISKA_ENABLED", value)
    assert not riska_enabled()


def test_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("RISKA_ENABLED", "true")
    assert riska_enabled()
    require_riska_enabled()


@pytest.mark.asyncio
async def test_all_disabled_endpoints_without_database(monkeypatch):
    monkeypatch.delenv("RISKA_ENABLED", raising=False)
    app = FastAPI()
    for routes in (router, execution_router, admin_router):
        app.include_router(routes)

    async def forbidden_db():
        pytest.fail("Disabled Riska must not open a database session")

    app.dependency_overrides[get_db] = forbidden_db
    # Derive requests from OpenAPI so future Riska endpoints are covered too.
    schema = app.openapi()
    assert len(schema["paths"]) == 10
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path, operations in schema["paths"].items():
            for method in operations:
                response = await client.request(method.upper(), path.replace("{draft_id}", "old-draft"), json={})
                assert response.status_code == 404, (method, path, response.text)
                assert response.json() == {"detail": "Fitur tidak tersedia"}

        @app.get("/unrelated")
        def unrelated():
            return {"ok": True}

        assert (await client.get("/unrelated")).json() == {"ok": True}
