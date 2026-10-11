"""Financial parser regressions: colloquial amounts, incomplete data and ambiguous writes."""
from datetime import date
import pytest
from modules.siabumdes.application.riska_language import amount, extract_operation, intent, report_range

TODAY = date(2026, 10, 11)


def test_natural_purchase_and_multiword_names():
    message = 'Riska, input barang masuk buat SKU-012 sebanyak 5 pcs harga 20ribu dari pemasok 3 hari ini'
    assert intent(message) == 'masuk'
    assert extract_operation(message, 'masuk', TODAY) == {
        'produk': 'SKU-012', 'qty': '5', 'harga': '20000', 'pemasok': '3', 'tanggal': '2026-10-11'}
    data = extract_operation('barang masuk produk "Daster Arab Jumbo" sebanyak 2 pcs dari pemasok "Toko Makmur"', 'masuk', TODAY)
    assert data == {'produk': 'Daster Arab Jumbo', 'qty': '2', 'pemasok': 'Toko Makmur'}
    assert extract_operation('20 ribu', 'masuk', TODAY, 'harga') == {'harga': '20000'}
    assert extract_operation('hari ini', 'masuk', TODAY, 'tanggal') == {'tanggal': '2026-10-11'}


@pytest.mark.parametrize('raw,expected', [('20ribu', '20000'), ('1,5 juta', '1500000.0'), ('20.000', '20000'), ('25000.50', '25000.50')])
def test_amounts(raw, expected):
    assert amount(raw) == expected


@pytest.mark.parametrize('text', ['barang masuk SKU-012 sebanyak 5 qty 8', 'barang masuk SKU-012 harga 20ribu harga 30ribu',
                                  'barang masuk SKU-012 tanggal 2026-10-11 tanggal 2026-10-12', 'masuk qty=1 qty=2'])
def test_ambiguous_write_values_are_not_guessed(text):
    with pytest.raises(ValueError):
        extract_operation(text, 'masuk', TODAY)


def test_negation_and_report_periods():
    assert intent('jangan tutup buku bulan ini') == 'cancel'
    assert report_range('laba rugi September 2026', TODAY) == (date(2026, 9, 1), date(2026, 9, 30))
    assert report_range('laba rugi dari 2026-01-01 sampai 2026-09-30', TODAY) == (date(2026, 1, 1), date(2026, 9, 30))
    with pytest.raises(ValueError):
        report_range('laba rugi September dan Oktober 2026', TODAY)


@pytest.mark.parametrize('text', ['10-10-2026', '10/10/2026', '10 Oktober 2026', 'kemarin'])
def test_simple_transaction_dates(text):
    assert extract_operation(text, 'masuk', TODAY, 'tanggal') == {'tanggal': '2026-10-10'}


def test_conflicting_relative_and_explicit_dates_are_not_guessed():
    with pytest.raises(ValueError):
        extract_operation('hari ini tanggal 10 Oktober 2026', 'masuk', TODAY)
    assert extract_operation('tanggal 10', 'masuk', TODAY, 'tanggal') == {}
    with pytest.raises(ValueError):
        extract_operation('31 Februari 2026', 'masuk', TODAY, 'tanggal')
