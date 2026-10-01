"""Ganti password harus menerbitkan cookie baru, bukan membatalkan sesi sendiri."""
import asyncio
import os
from types import SimpleNamespace

from fastapi import Response

# shared.database raises at import when DATABASE_URL is unset (CI leaves it unset).
if not (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")):
    os.environ["DATABASE_URL"] = "postgresql://placeholder@localhost/placeholder"

from modules.siabumdes.adapters.api.v1.auth_router import (
    ChangePasswordRequest,
    change_password,
)
from shared.security import decode_access_token, hash_password


def test_change_password_reissues_valid_session_cookie(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-unit-tests")
    user = SimpleNamespace(
        id="u1", role="admin", name="N", unit_usaha_id=None,
        password_hash=hash_password("temporary-pass"),
        must_change_password=True, session_version=1,
    )
    response = Response()
    payload = ChangePasswordRequest(current_password="temporary-pass", new_password="new-secret-pass")

    result = asyncio.run(change_password(payload, response, user, None))

    assert result == {"ok": True}
    assert user.must_change_password is False
    assert user.session_version == 2
    cookie = response.headers["set-cookie"]
    token = cookie.split("=", 1)[1].split(";", 1)[0]
    assert decode_access_token(token, expected_tenant="bumdes")["sv"] == user.session_version
