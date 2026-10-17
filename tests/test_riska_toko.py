from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from modules.siabumdes.adapters.api.v1 import riska_crud as crud
from modules.siabumdes.application.toko_transactions import default_week_date


def test_parse_toko_resources():
    assert crud.parse_command('tambah data toko')[:2] == ('toko', 'create')
    assert crud.parse_command('hapus isian toko')[:2] == ('isian', 'delete')


def test_toko_access_follows_role():
    crud.authorize(SimpleNamespace(role='pengelola'), 'isian', 'create')
    with pytest.raises(HTTPException):
        crud.authorize(SimpleNamespace(role='pengawas'), 'toko', 'create')


def test_default_week_date_matches_ui():
    assert default_week_date('2026-10', 1) == '2026-10-10'  # Senin–Sabtu 5–10 Okt
    assert default_week_date('2026-10', 5) == '2026-10-31'  # tanpa Minggu ke-5 -> Sabtu minggu ke-4
    assert default_week_date('2026-02', 4) == '2026-02-28'
