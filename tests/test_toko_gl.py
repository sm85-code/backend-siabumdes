from datetime import date
from decimal import Decimal

from modules.siabumdes.toko.application import gl
from modules.siabumdes.toko.application.journal import JournalRow

KAS, PEND, BEBAN, HPP, PERS, PIUT_L, UTANG, MODAL = (
    ("1.1.01.15", "Kas", "kas"), ("4.1.01.15", "Pendapatan", "pendapatan"), ("6.2.01.51", "Beban", "beban"),
    ("5.1.01.15", "HPP", "beban"), ("1.1.05.15", "Persediaan", "stok"), ("1.1.09.15", "Kasbon", "piutang_lainnya"),
    ("2.1.01.15", "Utang", "utang"), ("3.1.01.15", "Modal", "modal"))


def j(d, amt, debit, credit, ref="", typ="", closing=False):
    return JournalRow(ref or str(d), d, "", Decimal(amt), *debit, *credit, ref, typ, closing)


ROWS = [
    j(date(2026, 9, 1), 30_000_000, KAS, MODAL),
    j(date(2026, 9, 29), 100, KAS, PEND, "TOKO:a"),
    j(date(2026, 10, 3), 50, KAS, PEND, "TOKO:a"),  # 3 Okt -> Minggu ke-4 Sep
    j(date(2026, 10, 5), 1000, KAS, PEND, "TOKO:a"),
    j(date(2026, 10, 6), 70, KAS, PEND, "TOKO:lain"),
    j(date(2026, 10, 7), 400, PERS, KAS, "stock-in:h1", "inventory_purchase"),
    j(date(2026, 10, 8), 300, KAS, PEND, "stock-out-rev:h2", "inventory_sale_revenue"),
    j(date(2026, 10, 8), 200, HPP, PERS, "stock-out:h2", "inventory_cogs"),
    j(date(2026, 10, 11), 20, BEBAN, PERS, "stock-out-internal:c", "inventory_internal_use"),  # Minggu -> Minggu ke-1
    j(date(2026, 10, 12), 90, BEBAN, KAS, "TOKO:b"),
    j(date(2026, 10, 13), 15, BEBAN, KAS),  # manual
    j(date(2026, 10, 14), 40, PIUT_L, KAS, "TOKO:k", "kasbon_unit5"),
    j(date(2026, 10, 16), 5000, MODAL, PEND, "", "jurnal_penutup", True),
    j(date(2026, 10, 16), 10, BEBAN, KAS, "BAGIHASIL-1", "bagi_hasil"),
]


def test_laba_rugi_mingguan_dari_jurnal():
    lr = gl.weekly_laba_rugi(ROWS, "2026-10", {"lain"})
    assert lr["saldo"]["w1"] == 1000 and lr["lain"]["w1"] == 70 and lr["jual"]["w1"] == 300
    assert lr["hpp"]["w1"] == 200 and lr["internal"]["w1"] == 20
    assert lr["umum"]["w2"] == 90 and lr["trx_out"]["w2"] == 15  # bagi hasil & penutup tidak dihitung
    assert lr["saldo"]["jumlah"] == 1000  # 3 Okt ikut September
    assert gl.weekly_laba_rugi(ROWS, "2026-09", set())["saldo"]["w4"] == 150


def test_neraca_saldo_akun_dan_laba():
    p = gl.position(ROWS, date(2026, 10, 18), "2026-10")
    assert p["modal"] == 30_000_000 and p["piutang_lainnya"] == 40
    assert p["stok"] == 400 - 200 - 20
    assert p["laba_lalu"] == 150 - 10  # s.d. 4 Okt, dikurangi bagi hasil yang dibayar dari Kas
    assert p["laba_berjalan"] == 1000 + 70 + 300 - 200 - 20 - 90 - 15
    # Kas = 2.124.500 (30 Sep) + pergerakan Kas sejak 1 Okt; jurnal Kas September diabaikan.
    kas = 2_124_500 + 50 + 1000 + 70 - 400 + 300 - 90 - 15 - 40 - 10
    assert p["kas"] == kas
    assert gl.kas_at(ROWS, date(2026, 9, 29)) == 30_000_100  # sebelum saldo awal: buku besar
    k = gl.kas_detail(ROWS, date(2026, 10, 5), date(2026, 10, 18))
    assert k["kas_awal"] == 2_124_550 and k["kas_akhir"] == kas
    sep = gl.kas_detail(ROWS, date(2026, 9, 28), date(2026, 10, 4))
    assert sep["kas_awal_date"] == date(2026, 9, 30) and sep["kas_awal"] == 2_124_500
    assert sep["masuk_perduitan"] == 50 and sep["kas_akhir"] == 2_124_550
    assert k["masuk_perduitan"] == 1070 and k["keluar_perduitan"] == 90 and k["keluar_kasbon"] == 40
    assert k["masuk_penjualan_tunai"] == 300 and k["keluar_pembelian_tunai"] == 400 and k["keluar_transaksi"] == 25
    s = gl.stok_detail(ROWS, date(2026, 10, 5), date(2026, 10, 18), {"h1"})
    assert s == {"stok_awal": 0, "masuk_modal_awal": 400, "keluar_penjualan": -200, "keluar_internal": -20, "stok_akhir": 180}
