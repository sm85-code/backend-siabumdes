"""Unit tests for SIABUMDES money JSON string serialization."""
from __future__ import annotations

from decimal import Decimal

from modules.siabumdes.money_json import money_str, stringify_money_fields


def test_money_str_decimal_and_float():
    assert money_str(Decimal("1500.5")) == "1500.50"
    assert money_str(1500.5) == "1500.50"
    assert money_str(0) == "0.00"
    assert money_str(None) == "0.00"
    assert money_str("1234.5") == "1234.50"


def test_stringify_money_fields_allowlist():
    payload = {
        "amount": 1000.5,
        "total_pendapatan": Decimal("2000"),
        "share_pades": 30.0,  # percentage — must stay float
        "total_transactions": 7,  # count — not in allowlist
        "nested": {"laba_bersih": 99.1, "label": "x"},
        "alokasi": {"pengurus": 10.0, "penasihat": 5},
        "items": [{"amount": 1}, {"amount": "2.00"}],
    }
    out = stringify_money_fields(payload)
    assert out["amount"] == "1000.50"
    assert out["total_pendapatan"] == "2000.00"
    assert out["share_pades"] == 30.0
    assert out["total_transactions"] == 7
    assert out["nested"]["laba_bersih"] == "99.10"
    assert out["nested"]["label"] == "x"
    assert out["alokasi"]["pengurus"] == "10.00"
    assert out["alokasi"]["penasihat"] == "5.00"
    assert out["items"][0]["amount"] == "1.00"
    assert out["items"][1]["amount"] == "2.00"  # already str
