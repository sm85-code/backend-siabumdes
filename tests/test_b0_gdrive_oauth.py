"""B0 security: GDrive OAuth callback hardening.

No HTTP test client in this suite: exercise route dependency wiring and pure
helpers directly.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

# shared.database raises at import when DATABASE_URL is unset (CI leaves it
# unset). transaction_router pulls that in — use a placeholder so collection
# succeeds; these tests never open a real DB connection.
if not (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")):
    os.environ["DATABASE_URL"] = "postgresql://placeholder@localhost/placeholder"

from modules.siabumdes.adapters.api.v1 import transaction_router as tx


def test_gdrive_oauth_callback_requires_admin_role_dependency():
    route = next(r for r in tx.router.routes if r.path == "/api/admin/gdrive/oauth-callback")
    # require_roles returns an inner `_inner` callable as the dependency
    role_deps = [
        dep for dep in route.dependant.dependencies if getattr(dep.call, "__name__", "") == "_inner"
    ]
    assert role_deps, "oauth-callback must require admin via require_roles"


def test_gdrive_oauth_state_roundtrip(monkeypatch):
    monkeypatch.setattr(tx, "JWT_SECRET", "test-secret-for-gdrive-state")
    state = tx._make_gdrive_oauth_state()
    tx._verify_gdrive_oauth_state(state)  # does not raise


def test_gdrive_oauth_state_rejects_tampered(monkeypatch):
    monkeypatch.setattr(tx, "JWT_SECRET", "test-secret-for-gdrive-state")
    state = tx._make_gdrive_oauth_state()
    with pytest.raises(HTTPException) as exc:
        tx._verify_gdrive_oauth_state(state + "x")
    assert exc.value.status_code == 403


def test_gdrive_oauth_state_rejects_wrong_purpose(monkeypatch):
    monkeypatch.setattr(tx, "JWT_SECRET", "test-secret-for-gdrive-state")
    bad = jwt.encode(
        {"purpose": "other", "exp": 9999999999},
        "test-secret-for-gdrive-state",
        algorithm=tx.JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        tx._verify_gdrive_oauth_state(bad)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_gdrive_oauth_callback_response_omits_refresh_token(monkeypatch):
    monkeypatch.setattr(tx, "JWT_SECRET", "test-secret-for-gdrive-state")
    state = tx._make_gdrive_oauth_state()

    fake_creds = SimpleNamespace(refresh_token="rt-super-secret")
    fake_flow = MagicMock()
    fake_flow.credentials = fake_creds
    fake_flow.fetch_token = MagicMock()

    req = SimpleNamespace(base_url="https://api.example/")
    admin = SimpleNamespace(role="admin")

    with patch.object(tx, "oauth_flow", return_value=fake_flow):
        with patch.object(tx.logger, "warning") as warn:
            out = await tx.gdrive_oauth_callback(req, code="auth-code", state=state, _=admin)

    assert "refresh_token" not in out
    assert out.get("ok") is True
    assert "TIDAK dikirim" in out["detail"] or "tidak" in out["detail"].lower()
    # Token may appear in server log for ops bootstrap — never in HTTP body.
    assert warn.called
    assert "rt-super-secret" not in str(out)


@pytest.mark.asyncio
async def test_gdrive_oauth_callback_rejects_bad_state(monkeypatch):
    monkeypatch.setattr(tx, "JWT_SECRET", "test-secret-for-gdrive-state")
    req = SimpleNamespace(base_url="https://api.example/")
    with pytest.raises(HTTPException) as exc:
        await tx.gdrive_oauth_callback(req, code="auth-code", state="nope", _=SimpleNamespace())
    assert exc.value.status_code == 403
