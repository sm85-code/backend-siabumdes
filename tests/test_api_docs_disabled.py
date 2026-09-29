"""Public API docs (/docs, /redoc, /openapi.json) are off unless ENABLE_API_DOCS=true."""
from __future__ import annotations

import asyncio
import importlib
import os

import pytest

# shared.database raises at import when DATABASE_URL is unset (CI leaves it
# unset); a placeholder is enough since no connection is opened here.
if not (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")):
    os.environ["DATABASE_URL"] = "postgresql://placeholder@localhost/placeholder"


def _get_status(app, path: str) -> int:
    """Minimal ASGI GET (no httpx dependency, no lifespan -> no Postgres)."""
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    return start["status"]


def _load_app(monkeypatch, value: str | None):
    if value is None:
        monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    else:
        monkeypatch.setenv("ENABLE_API_DOCS", value)
    import main

    return importlib.reload(main).app


@pytest.fixture(autouse=True)
def _restore_main(monkeypatch):
    yield
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    import main

    importlib.reload(main)


def _paths(app) -> set[str]:
    """Flatten app.routes (FastAPI >=0.14x wraps include_router() entries)."""
    out: set[str] = set()
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        if contexts is not None:
            out.update(c.path for c in contexts())
        elif getattr(r, "path", None):
            out.add(r.path)
    return out


@pytest.mark.parametrize("value", [None, "", "false", "0", "no"])
def test_docs_disabled_by_default(monkeypatch, value):
    app = _load_app(monkeypatch, value)
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    assert not {"/docs", "/redoc", "/openapi.json"} & _paths(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert _get_status(app, path) == 404, path
    # Real API routes are still registered.
    assert "/health" in _paths(app)
    assert "/api/public/summary" in _paths(app)


@pytest.mark.parametrize("value", ["true", "1", "yes", "TRUE"])
def test_docs_can_be_enabled(monkeypatch, value):
    app = _load_app(monkeypatch, value)
    assert {"/docs", "/redoc", "/openapi.json"} <= _paths(app)
    assert _get_status(app, "/openapi.json") == 200
    assert _get_status(app, "/docs") == 200
