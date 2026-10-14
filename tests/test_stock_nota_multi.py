"""Nota multi-baris: satu jurnal + satu Purchase; baris gagal disebut nomornya."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1:5432/siabumdes_test")

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.siabumdes.inventory.application import stock_mixin
from modules.siabumdes.inventory.application.stock_mixin import HppMismatchError, InventoryStockMixin
from modules.siabumdes.inventory.infrastructure.models import Purchase


def _svc(products):
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(name="Toko A"))
    session.scalar = AsyncMock(side_effect=list(products))
    session.flush = AsyncMock()
    added = []

    def add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = f"id{len(added)}"
        added.append(obj)

    session.add = add
    svc = InventoryStockMixin()
    svc.session = session
    svc.finance = MagicMock(record_inventory_journal=AsyncMock())
    return svc, added


def _p(pid, qty, cost):
    return SimpleNamespace(id=pid, sku=pid.upper(), name=pid, unit_of_measure="kg", qty_on_hand=qty, cost_price=Decimal(cost))


HEADER = dict(movement_date=date(2026, 10, 14), unit_usaha_id="u1", debit_account_code="1",
              credit_account_code="2", vendor_id="v1")


@pytest.mark.asyncio
async def test_nota_satu_jurnal_satu_purchase():
    svc, added = _svc([_p("oyek", 0, "0"), _p("gula", 5, "6000")])
    with patch.object(stock_mixin, "validate_coa_codes", AsyncMock()):
        cards = await svc.stock_in_nota(lines=[
            {"product_id": "oyek", "quantity": 10, "unit_cost": Decimal("5000")},
            {"product_id": "gula", "quantity": 2, "unit_cost": Decimal("6000")},
        ], **HEADER)
    assert len(cards) == 2 and {c.nota_id for c in cards} == {cards[0].id}
    assert {c.reference for c in cards} == {f"stock-in:{cards[0].id}"}
    svc.finance.record_inventory_journal.assert_awaited_once()
    assert svc.finance.record_inventory_journal.await_args.kwargs["amount"] == Decimal("62000.00")
    purchases = [o for o in added if isinstance(o, Purchase)]
    assert len(purchases) == 1 and purchases[0].total_amount == Decimal("62000.00")


@pytest.mark.asyncio
async def test_baris_harga_beda_ditolak_dengan_nomor_baris():
    svc, _ = _svc([_p("oyek", 0, "0"), _p("gula", 5, "6000")])
    with pytest.raises(HppMismatchError, match=r"^Baris 2 \[GULA\]"):
        await svc.stock_in_nota(lines=[
            {"product_id": "oyek", "quantity": 10, "unit_cost": Decimal("5000")},
            {"product_id": "gula", "quantity": 2, "unit_cost": Decimal("7000")},
        ], **HEADER)
    svc.finance.record_inventory_journal.assert_not_awaited()
