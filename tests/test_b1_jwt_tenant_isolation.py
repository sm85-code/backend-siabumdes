"""SIABUMDES B1 — JWT tenant isolation (aud/iss + optional per-tenant secret).

This backend only issues ``bumdes`` tokens, but the aud/iss claims and secret
resolution are kept identical to sm85-arch so tokens issued there stay valid
here (same secret) and tokens minted for any other audience are rejected.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException

# Arbitrary non-bumdes audience; stands in for any other app sharing a secret.
FOREIGN_TENANT = "other_app"


def _reload_security(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    # Ensure shared fallback is always present for encode.
    if "JWT_SECRET" not in env:
        monkeypatch.setenv("JWT_SECRET", "shared-secret-for-tests")
    from shared import config as config_module

    importlib.reload(config_module)
    import shared.security as security_module

    importlib.reload(security_module)
    return security_module


def test_bumdes_token_claims_round_trip(monkeypatch):
    sec = _reload_security(monkeypatch, JWT_SECRET="shared-secret-for-tests", JWT_SECRET_BUMDES=None)
    token = sec.create_access_token("u1", "admin", 1)
    payload = sec.decode_access_token(token, expected_tenant="bumdes")
    assert payload["sub"] == "u1"
    assert payload["aud"] == "bumdes"
    assert payload["iss"] == "sm85:bumdes"


def test_bumdes_token_rejected_for_foreign_audience(monkeypatch):
    sec = _reload_security(monkeypatch, JWT_SECRET="shared-secret-for-tests")
    token = sec.create_access_token("u1", "admin", 1, tenant="bumdes")
    with pytest.raises(HTTPException) as exc:
        sec.decode_access_token(token, expected_tenant=FOREIGN_TENANT)
    assert exc.value.status_code == 401


def test_foreign_token_rejected_by_bumdes_decode(monkeypatch):
    sec = _reload_security(monkeypatch, JWT_SECRET="shared-secret-for-tests")
    token = sec.create_access_token("u1", "admin", 1, tenant=FOREIGN_TENANT)
    with pytest.raises(HTTPException) as exc:
        sec.decode_access_token(token, expected_tenant="bumdes")
    assert exc.value.status_code == 401


def test_tenant_specific_secret_preferred(monkeypatch):
    sec = _reload_security(
        monkeypatch,
        JWT_SECRET="shared-secret-for-tests",
        JWT_SECRET_BUMDES="bumdes-only-secret",
    )
    assert sec.jwt_secret_for("bumdes") == "bumdes-only-secret"
    bumdes_token = sec.create_access_token("u1", "admin", 1, tenant="bumdes")
    assert sec.decode_access_token(bumdes_token, expected_tenant="bumdes")["sub"] == "u1"

    # A token signed with the shared secret (and even the right aud/iss) no
    # longer verifies once JWT_SECRET_BUMDES is set to a distinct value.
    from jose import jwt as jose_jwt

    forged = jose_jwt.decode(bumdes_token, "bumdes-only-secret", algorithms=["HS256"], options={"verify_aud": False})
    shared_signed = jose_jwt.encode(forged, "shared-secret-for-tests", algorithm="HS256")
    with pytest.raises(HTTPException):
        sec.decode_access_token(shared_signed, expected_tenant="bumdes")


def test_fallback_to_shared_secret_when_tenant_unset(monkeypatch):
    sec = _reload_security(
        monkeypatch,
        JWT_SECRET="shared-secret-for-tests",
        JWT_SECRET_BUMDES=None,
    )
    assert sec.jwt_secret_for("bumdes") == "shared-secret-for-tests"
    token = sec.create_access_token("u1", "admin", 1, tenant="bumdes")
    payload = sec.decode_access_token(token, expected_tenant="bumdes")
    assert payload["aud"] == "bumdes"


def test_cookie_name_is_fixed_not_env_configurable(monkeypatch):
    # Not a secret -- fixed in code so it can never end up unset/mismatched
    # between the code that sets it and the code that reads it back.
    monkeypatch.setenv("JWT_COOKIE_NAME", "something-else")
    sec = _reload_security(monkeypatch, JWT_SECRET="shared-secret-for-tests")
    assert sec.JWT_COOKIE_NAME == "bumdes_token"


def test_legacy_token_without_aud_iss_still_accepted(monkeypatch):
    """Migration window: pre-B1 tokens (no aud/iss) must not lock out live users."""
    sec = _reload_security(monkeypatch, JWT_SECRET="shared-secret-for-tests")
    from datetime import datetime, timedelta, timezone

    from jose import jwt as jose_jwt

    now = datetime.now(timezone.utc)
    legacy = jose_jwt.encode(
        {
            "sub": "legacy-user",
            "role": "admin",
            "sv": 1,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        "shared-secret-for-tests",
        algorithm="HS256",
    )
    payload = sec.decode_access_token(legacy, expected_tenant="bumdes")
    assert payload["sub"] == "legacy-user"
    assert "aud" not in payload
