"""Urutan guard periode: tutup buku > kunci periode (non-admin) > blokir per pengguna."""
import asyncio
import os
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

# shared.database raises at import when DATABASE_URL is unset (CI leaves it unset).
if not (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")):
    os.environ["DATABASE_URL"] = "postgresql://placeholder@localhost/placeholder"

from modules.siabumdes.adapters.api import scope  # noqa: E402


def _run(monkeypatch, *, role, closed=False, locked=False, blocked=()):
    async def fake_control(_session):
        return SimpleNamespace(recording_locked=False)

    async def fake_closed(_s, _p, _g):
        return closed

    async def fake_locked(_s, _p, _g):
        return locked

    monkeypatch.setattr(scope, "get_system_control", fake_control)
    monkeypatch.setattr(scope, "_is_period_closed", fake_closed)
    monkeypatch.setattr(scope, "_is_period_locked", fake_locked)
    user = SimpleNamespace(role=role, blocked_periods=list(blocked))
    return asyncio.run(scope.assert_can_mutate_period(None, user, date(2026, 9, 10), None))


def _detail(monkeypatch, **kw):
    with pytest.raises(HTTPException) as exc:
        _run(monkeypatch, **kw)
    return exc.value.status_code, exc.value.detail


def test_closed_message_wins_over_user_block(monkeypatch):
    code, detail = _detail(monkeypatch, role="pengelola", closed=True, blocked=["2026-09"])
    assert code == 400 and "sudah ditutup" in detail


def test_closed_blocks_admin_too(monkeypatch):
    code, _ = _detail(monkeypatch, role="admin", closed=True)
    assert code == 400


def test_period_lock_blocks_non_admin_but_not_admin(monkeypatch):
    code, detail = _detail(monkeypatch, role="pengelola", locked=True)
    assert code == 403 and "dikunci oleh Admin" in detail
    _run(monkeypatch, role="admin", locked=True)  # admin boleh mengoreksi


def test_user_block_still_applies_to_non_admin_only(monkeypatch):
    code, detail = _detail(monkeypatch, role="pengelola", blocked=["2026-09"])
    assert code == 403 and "akun ini" in detail
    _run(monkeypatch, role="admin", blocked=["2026-09"])


def test_open_period_allowed(monkeypatch):
    _run(monkeypatch, role="pengelola")
