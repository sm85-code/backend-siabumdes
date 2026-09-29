"""Regression tests for auth/session primitives.

Note: this file previously imported `services.jwt_service`, `database._matches`,
`database._query_condition`, and `models.transactions.TransactionCreate` — none
of which exist in the current codebase (leftovers from an earlier, pre-Postgres
iteration of the app). Those tests always failed at collection and were never
actually run. They have been replaced with tests against the real, current
modules (`shared.security`).
"""


def test_jwt_round_trip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-unit-tests")
    import importlib

    from shared import config as config_module

    importlib.reload(config_module)
    import shared.security as security_module

    importlib.reload(security_module)

    token = security_module.create_access_token("user-1", "admin", session_version=1)
    payload = security_module.decode_access_token(token, expected_tenant="bumdes")

    assert payload["sub"] == "user-1"
    assert payload["role"] == "admin"
    assert payload["sv"] == 1
    assert payload["aud"] == "bumdes"
    assert payload["iss"] == "sm85:bumdes"
