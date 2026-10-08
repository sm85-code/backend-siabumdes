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
from modules.siabumdes.infrastructure.models import YieldPartner, YieldPayment, UnitUsaha, Transaction, JournalEntry, TransactionType, Account  # noqa: E402
from modules.siabumdes.application import yield_transactions as yt  # noqa: E402
from shared.database import get_db  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    unit = SimpleNamespace(id='u4', code='UU04', active=True)
    state = SimpleNamespace(user=SimpleNamespace(id='actor', name='Admin', role='admin', unit_usaha_id=None), unit=unit, rows={}, payments={}, finance={}, journals={}, locked=False, dates=[])

    accounts = {code: SimpleNamespace(id=code, code=code, category=category) for code, category in [('cash', 'aset'), ('income', 'pendapatan')]}
    state.kind = SimpleNamespace(debit='cash', credit='income')

    class Session:
        async def execute(self, stmt):
            entity = stmt.column_descriptions[0]['entity']
            params = stmt.compile().params
            if entity is UnitUsaha:
                return SimpleNamespace(scalar_one_or_none=lambda: unit)
            if entity is Account:
                return SimpleNamespace(scalar_one_or_none=lambda: accounts.get(params['code_1']))
            if entity is YieldPartner and 'id_1' in params:
                row = state.rows.get(params['id_1'])
                return SimpleNamespace(scalar_one_or_none=lambda: row if row and row.unit_usaha_id == unit.id else None)
            if entity is YieldPayment:
                return SimpleNamespace(scalars=lambda: [p for p in state.payments.values() if p.year == params['year_1']])
            return SimpleNamespace(scalars=lambda: list(state.rows.values()))

        async def scalar(self, stmt):
            entity = stmt.column_descriptions[0]['entity']
            params = stmt.compile().params
            if entity is TransactionType:
                return state.kind
            if entity is Account:
                return accounts.get(params['code_1'])
            if entity is JournalEntry:
                return state.journals.get(params['transaction_id_1'])
            if entity is YieldPayment:
                return next((p for p in state.payments.values() if p.partner_id == params['partner_id_1'] and ('year_1' not in params or (p.year == params['year_1'] and p.month == params['month_1']))), None)

        async def get(self, model, key):
            if model is UnitUsaha:
                return unit
            return state.finance.get(key) if model is Transaction else state.rows.get(key)

        def add(self, row):
            row.id = row.id or str(id(row))
            if isinstance(row, YieldPartner):
                state.rows[row.id] = row
            elif isinstance(row, Transaction):
                state.finance[row.id] = row
            elif isinstance(row, JournalEntry):
                state.journals[row.transaction_id] = row
            elif isinstance(row, YieldPayment):
                state.payments[(row.partner_id, row.year, row.month)] = row
            else:
                raise AssertionError(type(row))

        async def flush(self):
            pass

        async def delete(self, row):
            if isinstance(row, YieldPayment):
                del state.payments[(row.partner_id, row.year, row.month)]
            elif isinstance(row, JournalEntry):
                del state.journals[row.transaction_id]
            elif isinstance(row, Transaction):
                del state.finance[row.id]
            else:
                del state.rows[row.id]

    async def period(session, actor, tx_date, unit_id):
        state.dates.append(tx_date)
        if state.locked:
            from fastapi import HTTPException
            raise HTTPException(403, 'Période terkunci')

    monkeypatch.setattr(yt, 'assert_can_mutate_period', period)
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


def test_linked_finance_updates_balanced_journal_and_period_guards(client):
    http, state = client
    pid = http.post('/api/imbal-hasil/mitra', json={'name': 'Mitra', 'capital': '1000000'}).json()['id']
    url = f'/api/imbal-hasil/mitra/{pid}/pembayaran'
    body = {'year': 2026, 'month': 1, 'automatic': True, 'transaction_date': '2026-10-09'}
    assert http.put(url, json=body).status_code == 200
    tx = next(iter(state.finance.values()))
    assert tx.description == 'Pembayaran Imbal Hasil - Mitra - 01/2026'
    assert tx.transaction_type == 'pendapatan_bagi_hasil_unit4'
    assert tx.unit_usaha_id == 'u4' and str(tx.date) == '2026-10-09'
    journal = state.journals[tx.id]
    assert [(i.side, i.amount) for i in journal.items] == [('debit', Decimal('30000')), ('credit', Decimal('30000'))]
    assert http.put(url, json={**body, 'automatic': False, 'amount': '40000', 'transaction_date': '2026-10-10'}).status_code == 200
    assert len(state.finance) == len(state.journals) == 1
    assert tx.amount == Decimal('40000')
    assert state.journals[tx.id].entry_date == tx.date
    state.locked = True
    assert http.put(url, json=body).status_code == 403
    assert http.request('DELETE', url, json=body).status_code == 403
    assert tx.amount == Decimal('40000')
    state.locked = False
    assert http.delete(f'/api/imbal-hasil/mitra/{pid}').status_code == 409
    assert http.request('DELETE', url, json=body).status_code == 204
    assert not state.finance and not state.journals and not state.payments


def test_missing_configuration_and_zero_amount_fail_before_finance_write(client):
    http, state = client
    pid = http.post('/api/imbal-hasil/mitra', json={'name': 'M', 'capital': '1000000'}).json()['id']
    url = f'/api/imbal-hasil/mitra/{pid}/pembayaran'
    assert http.put(url, json={'year': 2026, 'month': 1, 'amount': '0'}).status_code == 422
    state.kind = None
    assert http.put(url, json={'year': 2026, 'month': 1, 'automatic': True}).status_code == 422
    assert not state.finance and not state.payments


def test_manual_transaction_route_rejects_yield_reference():
    from fastapi import HTTPException
    from modules.siabumdes.adapters.api.v1.transaction_router import _assert_not_bagi_hasil
    with pytest.raises(HTTPException) as caught:
        _assert_not_bagi_hasil('YIELD:partner:2026:01')
    assert caught.value.status_code == 400
    _assert_not_bagi_hasil('manual-reference')
