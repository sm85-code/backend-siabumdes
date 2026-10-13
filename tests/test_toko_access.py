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


class _DeleteSession:
    def __init__(self, count):
        self.count, self.deleted = count, False

    async def get(self, model, key, **kwargs):
        return SimpleNamespace(id=key, name='Penjualan')

    async def scalar(self, stmt):
        return self.count

    async def delete(self, obj):
        self.deleted = True

    async def commit(self):
        pass


def _delete_client(session):
    app = FastAPI()
    app.include_router(tr.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='x', username='x', role='admin', unit_usaha_id=None)

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_delete_item_with_entries_rejected():
    s = _DeleteSession(2)
    r = _delete_client(s).delete('/api/v1/toko/items/a')
    assert r.status_code == 409 and 'tidak dapat dihapus' in r.json()['detail']
    assert not s.deleted


def test_delete_item_without_entries_allowed():
    s = _DeleteSession(0)
    assert _delete_client(s).delete('/api/v1/toko/items/a').status_code == 200
    assert s.deleted
