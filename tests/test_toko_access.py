"""Manajemen Toko: hanya admin/direktur/bendahara/pengelola UU05."""
import os
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'postgresql://user:pass@127.0.0.1:5432/test')

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.siabumdes.adapters.api.deps import get_current_user  # noqa: E402
from modules.siabumdes.adapters.api.v1 import toko_router as tr  # noqa: E402
from shared.database import get_db  # noqa: E402

UNITS = {'u5': SimpleNamespace(id='u5', code='UU05'), 'u4': SimpleNamespace(id='u4', code='UU04')}


class _Session:
    async def get(self, model, key, **kwargs):
        return UNITS.get(key)

    async def execute(self, stmt):
        return SimpleNamespace(scalars=lambda: [])


def _client(role, unit_id=None):
    app = FastAPI()
    app.include_router(tr.router)
    user = SimpleNamespace(id='x', username='x', role=role, unit_usaha_id=unit_id, active=True)
    app.dependency_overrides[get_current_user] = lambda: user

    async def _db():
        yield _Session()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


@pytest.mark.parametrize('role', ['admin', 'direktur', 'bendahara'])
def test_core_roles_allowed(role):
    assert _client(role).get('/api/v1/toko/items').status_code == 200


def test_pengelola_unit5_allowed():
    assert _client('pengelola', 'u5').get('/api/v1/toko/items').status_code == 200


@pytest.mark.parametrize('unit_id', ['u4', None])
def test_pengelola_other_unit_denied(unit_id):
    assert _client('pengelola', unit_id).get('/api/v1/toko/items').status_code == 403


def test_other_role_denied():
    assert _client('pengawas').get('/api/v1/toko/items').status_code == 403


def test_bad_period_rejected():
    assert _client('admin').get('/api/v1/toko/entries', params={'period': '2026-13'}).status_code == 422
