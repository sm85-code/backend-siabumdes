"""Internal failures expose generic responses while retaining tracebacks in logs."""
from __future__ import annotations

import logging
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importing main creates an engine but these tests never connect or run lifespan.
if not (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")):
    os.environ["DATABASE_URL"] = "postgresql://placeholder@localhost/placeholder"


class InternalFailureForPrivacyTest(RuntimeError):
    pass


SECRET_MESSAGE = "private database credentials and query details"


def _assert_private_response(response, expected_status, expected_body):
    assert response.status_code == expected_status
    assert response.json() == expected_body
    for internal in (
        "error_type", "error_message", "path", "traceback",
        "InternalFailureForPrivacyTest", "RuntimeError", SECRET_MESSAGE,
        "Traceback (most recent call last)",
    ):
        assert internal not in response.text


def _assert_traceback_logged(caplog):
    assert any(
        record.name == "sm85.audit"
        and record.exc_info
        and isinstance(record.exc_info[1], InternalFailureForPrivacyTest)
        and str(record.exc_info[1]) == SECRET_MESSAGE
        for record in caplog.records
    )


def test_unhandled_500_hides_internal_details(caplog):
    from main import CsrfOriginMiddleware

    app = FastAPI()
    app.add_middleware(CsrfOriginMiddleware)

    @app.get("/private-failure-path")
    async def fail():
        raise InternalFailureForPrivacyTest(SECRET_MESSAGE)

    with caplog.at_level(logging.ERROR, logger="sm85.audit"):
        response = TestClient(app, raise_server_exceptions=False).get("/private-failure-path")

    _assert_private_response(response, 500, {"detail": "Terjadi kesalahan internal pada server"})
    assert "/private-failure-path" not in response.text
    _assert_traceback_logged(caplog)


@pytest.mark.parametrize("failure_stage", ["connect", "execute"])
def test_health_database_failure_hides_internal_details(monkeypatch, caplog, failure_stage):
    import main

    class FailedConnection:
        async def __aenter__(self):
            if failure_stage == "connect":
                raise InternalFailureForPrivacyTest(SECRET_MESSAGE)
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement):
            raise InternalFailureForPrivacyTest(SECRET_MESSAGE)

    monkeypatch.setattr(main, "engine", SimpleNamespace(connect=FailedConnection))
    # Exercise the real endpoint over HTTP without the application's startup hooks.
    app = FastAPI()
    app.add_api_route("/health", main.health, methods=["GET"])
    with caplog.at_level(logging.ERROR, logger="sm85.audit"):
        response = TestClient(app).get("/health")

    _assert_private_response(response, 503, {"status": "degraded", "database": "unavailable"})
    _assert_traceback_logged(caplog)
