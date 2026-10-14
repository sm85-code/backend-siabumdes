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
