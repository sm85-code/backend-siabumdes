"""Semua mutasi stok/utang/piutang menolak tanggal di periode tutup buku (sama seperti /stock-in)."""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from modules.siabumdes.adapters.api.v1 import uu05_inventory_router as inv

D = date(2026, 9, 10)


@pytest.mark.parametrize("fn,body", [
    (inv.stock_out, SimpleNamespace(unit_usaha_id="u", movement_date=D)),
    (inv.stock_out_internal, SimpleNamespace(unit_usaha_id="u", movement_date=D)),
    (inv.create_adjustment, SimpleNamespace(unit_usaha_id="u", adjustment_date=D)),
    (inv.pay_purchase, SimpleNamespace(unit_usaha_id="u", paid_date=D)),
    (inv.pay_sale, SimpleNamespace(unit_usaha_id="u", paid_date=D)),
    (inv.cancel_movement, SimpleNamespace(stock_card_id="c")),
    (inv.cancel_adjustment, SimpleNamespace(adjustment_id="a")),
])
def test_closed_period_rejected_before_service(monkeypatch, fn, body):
    seen = []

    async def closed(session, user, when, unit_id):
        seen.append(when)
        raise HTTPException(400, "Buku periode 2026-09 (UU05) sudah ditutup")

    async def unit(session, uid, user):
        return "u"

    class Session:
        async def get(self, model, key, **kw):
            if model is inv.StockCard:
                return SimpleNamespace(id=key, product_id="p", movement_date=D)
            if model is inv.StockAdjustment:
                return SimpleNamespace(id=key, product_id="p", adjustment_date=D)
            return SimpleNamespace(unit_usaha_id="u")

        async def scalars(self, stmt):
            return []

    class NoService:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            raise AssertionError("service tidak boleh dipanggil")

    monkeypatch.setattr(inv, "assert_can_mutate_period", closed)
    monkeypatch.setattr(inv, "resolve_unit_id", unit)
    monkeypatch.setattr(inv, "InventoryService", NoService)
    with pytest.raises(HTTPException) as err:
        asyncio.run(fn(body=body, user=SimpleNamespace(username="x"), session=Session()))
    assert "sudah ditutup" in err.value.detail and seen == [D]
