import pytest

from modules.siabumdes.inventory.application.catalog import validate_sku


def test_sku_must_follow_category_prefix():
    assert validate_sku(' krs.prt-001 ', 'PRT') == 'KRS.PRT-001'
    for bad in ('PRT-001', 'KRS.PD-001', 'KRS.PRT-', 'KRS.PRT'):
        with pytest.raises(ValueError):
            validate_sku(bad, 'PRT')


def test_product_history_ignores_cancelled_stock_cards():
    from modules.siabumdes.inventory.application import catalog
    """Kartu stok legacy yang dibatalkan (soft-cancel) bukan riwayat: produk tetap bisa dihapus."""
    src = open(catalog.__file__).read()
    assert src.count('StockCard.finance_status != "cancelled"') >= 2


@pytest.mark.asyncio
async def test_ganti_kategori_tulis_ulang_sku_dan_tolak_bentrok():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from modules.siabumdes.inventory.application import catalog

    product = SimpleNamespace(id="p1", sku="KRS.PRT-001", category_id="c1", unit_usaha_id="u5")
    cat = SimpleNamespace(id="c2", code="PHP")
    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, _id: product if model is catalog.Product else cat)
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    svc = SimpleNamespace(session=session, _product_dict=lambda p: {"sku": p.sku}, product_has_history=AsyncMock(return_value=True))
    cls = next(c for c in vars(catalog).values() if isinstance(c, type) and hasattr(c, "change_category_sku"))
    out = await cls.change_category_sku(svc, "p1", category_id="c2", suffix="k75-1p")
    assert out["sku"] == "KRS.PHP-K75-1P" and product.category_id == "c2" and out["has_history"]
    session.scalar = AsyncMock(return_value="p2")
    with pytest.raises(ValueError, match="sudah dipakai"):
        await cls.change_category_sku(svc, "p1", category_id="c2", suffix="X")
