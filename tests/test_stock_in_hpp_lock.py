"""Stock In tidak boleh menimpa HPP SKU yang masih punya stok."""
from decimal import Decimal
from types import SimpleNamespace

from modules.siabumdes.inventory.application.stock_mixin import stock_in_price_error


def _p(qty, cost):
    return SimpleNamespace(qty_on_hand=qty, cost_price=Decimal(cost))


def test_harga_sama_boleh():
    assert stock_in_price_error(_p(10, "5000"), Decimal("5000")) is None


def test_harga_beda_ditolak_saat_masih_ada_stok():
    msg = stock_in_price_error(_p(10, "5000"), Decimal("6000"))
    assert msg == "Harga beli berbeda dengan HPP SKU ini (Rp 5.000). Buat SKU baru untuk harga baru."


def test_stok_kosong_boleh_harga_baru():
    assert stock_in_price_error(_p(0, "5000"), Decimal("6000")) is None
    assert stock_in_price_error(_p(0, "0"), Decimal("6000")) is None
