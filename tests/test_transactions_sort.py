import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1:5432/siabumdes_test")

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from modules.siabumdes.adapters.api.v1.transaction_router import _tx_order


def _sql(order):
    return [str(o.compile(dialect=postgresql.dialect())) for o in order]


def test_sort_amount_desc_empty_last_stable_tiebreak():
    sql = _sql(_tx_order("amount", "desc"))
    assert "IS NULL" in sql[0] and sql[0].endswith("ASC")
    assert sql[1].endswith("amount DESC")
    assert sql[-1].endswith("id DESC")


def test_sort_text_blank_last():
    assert "trim" in _sql(_tx_order("description", "asc"))[0]


def test_default_and_invalid():
    assert _sql(_tx_order(None, None))[0].endswith("date DESC")
    with pytest.raises(HTTPException):
        _tx_order("password", "asc")
