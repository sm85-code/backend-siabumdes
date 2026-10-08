"""Imbal hasil access, exact monetary calculation and CRUD without journal writes."""
import os
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'postgresql://user:pass@127.0.0.1:5432/test')

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from modules.siabumdes.adapters.api.deps import get_current_user  # noqa: E402
from modules.siabumdes.adapters.api.v1 import yield_router as yr  # noqa: E402
from modules.siabumdes.infrastructure.models import YieldPartner, YieldPayment, UnitUsaha  # noqa: E402
from shared.database import get_db  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    unit = SimpleNamespace(id='u4', code='UU04', active=True)
    state = SimpleNamespace(user=SimpleNamespace(id='actor', name='Admin', role='admin', unit_usaha_id=None), unit=unit, rows={}, payments={})

    class Session:
        async def execute(self, stmt):
            if not hasattr(stmt, 'column_descriptions'):
                params = stmt.compile().params
                if stmt.is_insert:
                    key = (params['partner_id'], params['year'], params['month'])
                    state.payments[key] = SimpleNamespace(**{k: params[k] for k in ('partner_id', 'year', 'month', 'amount')})
                    return SimpleNamespace(rowcount=1)
                key = (params['partner_id_1'], params['year_1'], params['month_1'])
                return SimpleNamespace(rowcount=1 if state.payments.pop(key, None) else 0)
            if stmt.column_descriptions[0]['entity'] is YieldPayment:
                return SimpleNamespace(scalars=lambda: [p for p in state.payments.values() if p.year == stmt.compile().params['year_1']])
            if stmt.column_descriptions[0]['entity'] is UnitUsaha:
                return SimpleNamespace(scalar_one_or_none=lambda: unit)
            return SimpleNamespace(scalars=lambda: list(state.rows.values()))

        async def get(self, model, key):
            return unit if model is UnitUsaha else state.rows.get(key)

        def add(self, row):
            assert isinstance(row, YieldPartner)  # never a finance transaction
            row.id = row.id or str(len(state.rows) + 1)
            state.rows[row.id] = row

        async def flush(self):
            pass

        async def delete(self, row):
            del state.rows[row.id]

    async def audit(*args, **kwargs):
        pass

    monkeypatch.setattr(yr, 'record_audit', audit)
    app = FastAPI()
    app.include_router(yr.router)
    app.dependency_overrides[get_current_user] = lambda: state.user
    app.dependency_overrides[get_db] = lambda: Session()
    with TestClient(app) as http:
        yield http, state


def test_partner_crud_exact_yield_and_validation(client):
    http, state = client
    created = http.post('/api/imbal-hasil/mitra', json={'name': '  Mitra A  ', 'capital': '1000000.50'})
    assert created.status_code == 201
    assert created.json()['yield_amount'] == '30000.02'
    pid = created.json()['id']
    assert http.get('/api/imbal-hasil/mitra').json()['items'][0]['name'] == 'Mitra A'
    assert http.put(f'/api/imbal-hasil/mitra/{pid}', json={'name': 'B', 'capital': '2000000'}).json()['yield_amount'] == '60000.00'
    for capital in ('-1', '0', 'NaN', '1.001'):
        assert http.post('/api/imbal-hasil/mitra', json={'name': 'C', 'capital': capital}).status_code == 422
    assert http.post('/api/imbal-hasil/mitra', json={'name': '   ', 'capital': '1'}).status_code == 422
    assert http.delete(f'/api/imbal-hasil/mitra/{pid}').status_code == 204
    assert not state.rows


@pytest.mark.parametrize('role,unit_id,status', [('admin', None, 200), ('direktur', None, 200), ('bendahara', None, 200), ('pengelola', 'u4', 200), ('pengelola', 'u3', 403), ('pengelola', None, 403), ('pengawas', None, 403), ('penasihat', None, 403)])
def test_read_and_write_scope(client, role, unit_id, status):
    http, state = client
    state.user.role, state.user.unit_usaha_id = role, unit_id
    assert http.get('/api/imbal-hasil/mitra').status_code == status
    assert http.post('/api/imbal-hasil/mitra', json={'name': 'A', 'capital': '100'}).status_code == (201 if status == 200 else 403)
    if status == 403:
        assert http.put('/api/imbal-hasil/mitra/x', json={'name': 'B', 'capital': '100'}).status_code == 403
        assert http.delete('/api/imbal-hasil/mitra/x').status_code == 403


def test_inactive_and_foreign_partner_protection(client):
    http, state = client
    state.unit.active = False
    assert http.get('/api/imbal-hasil/mitra').status_code == 200
    assert http.post('/api/imbal-hasil/mitra', json={'name': 'A', 'capital': '100'}).status_code == 422
    state.unit.active = True
    state.rows['foreign'] = YieldPartner(id='foreign', unit_usaha_id='u3', name='Hidden', capital=Decimal('100'))
    assert http.delete('/api/imbal-hasil/mitra/foreign').status_code == 404
    assert http.put('/api/imbal-hasil/mitra/foreign', json={'name': 'B', 'capital': '100'}).status_code == 404



def test_payment_input_and_scope(client):
    http, state = client
    pid = http.post('/api/imbal-hasil/mitra', json={'name': 'P', 'capital': '1000000'}).json()['id']
    url = f'/api/imbal-hasil/mitra/{pid}/pembayaran'
    assert http.put(url, json={'year': 2026, 'month': 1, 'automatic': True}).json()['amount'] == '30000.00'
    assert http.put(url, json={'year': 2027, 'month': 1, 'amount': '12345.67'}).json()['amount'] == '12345.67'
    for payload in ({'year': 2026, 'month': 1}, {'year': 2026, 'month': 13, 'amount': '1'}, {'year': 2026, 'amount': '1'}, {'year': 2026, 'month': 1, 'amount': '-1'}):
        assert http.put(url, json=payload).status_code == 422
        assert http.request('DELETE', url, json=payload).status_code == 422
    assert http.request('DELETE', url, json={'year': 2026, 'month': 1, 'automatic': True}).status_code == 204
    state.user.role, state.user.unit_usaha_id = 'pengelola', 'u3'
    assert http.put(url, json={'year': 2026, 'month': 1, 'amount': '1'}).status_code == 403
    assert http.request('DELETE', url, json={'year': 2026, 'month': 1, 'amount': '1'}).status_code == 403


def test_payments_keep_years_separate_and_preserve_recorded_amount(client):
    http, _ = client
    pid = http.post('/api/imbal-hasil/mitra', json={'name': 'Mitra', 'capital': '1000000'}).json()['id']
    url = f'/api/imbal-hasil/mitra/{pid}/pembayaran'
    assert http.put(url, json={'year': 2026, 'month': 1, 'automatic': True, 'amount': '999'}).status_code == 200
    assert http.put(url, json={'year': 2027, 'month': 1, 'amount': '12000'}).status_code == 200
    http.put(f'/api/imbal-hasil/mitra/{pid}', json={'name': 'Mitra', 'capital': '2000000'})
    assert http.get('/api/imbal-hasil/mitra?year=2026').json()['items'][0]['payments'] == {'1': '30000.00'}
    assert http.get('/api/imbal-hasil/mitra?year=2027').json()['items'][0]['payments'] == {'1': '12000'}
    http.put(url, json={'year': 2026, 'month': 1, 'amount': '45000'})
    assert http.get('/api/imbal-hasil/mitra?year=2026').json()['items'][0]['payments'] == {'1': '45000'}
    assert http.request('DELETE', url, json={'year': 2026, 'month': 1, 'automatic': True}).status_code == 204
    assert http.get('/api/imbal-hasil/mitra?year=2026').json()['items'][0]['payments'] == {}
    assert http.get('/api/imbal-hasil/mitra?year=2027').json()['items'][0]['payments'] == {'1': '12000'}
