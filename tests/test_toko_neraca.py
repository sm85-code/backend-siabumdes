"""Neraca toko harus seimbang: Kas + Stok + Piutang = Utang + Modal + Laba."""
from datetime import date
from decimal import Decimal as D

from modules.siabumdes.toko.application.journal import JournalRow, classify, is_manual
from modules.siabumdes.toko.application.neraca import OPENING, OPENING_DATE, Data, Trade, is_piutang_lain, position, position_with_opening


def test_neraca_seimbang_skenario_lengkap():
    data = Data(
        # Belanja modal awal 10jt, pembelian kas 2jt, pembelian kredit 3jt (dibayar 1jt).
        purchases=[
            Trade(date(2026, 10, 1), D("10000000"), "modal_awal"),
            Trade(date(2026, 10, 6), D("2000000"), "cash"),
            Trade(date(2026, 10, 7), D("3000000"), "credit", [(date(2026, 10, 13), D("1000000"))]),
        ],
        # Penjualan tunai 1.5jt (HPP 1jt), piutang 2jt (HPP 1.2jt) dilunasi sebagian.
        sales=[
            Trade(date(2026, 10, 8), D("1500000"), "cash"),
            Trade(date(2026, 10, 9), D("2000000"), "piutang", [(date(2026, 10, 14), D("500000"))]),
        ],
        cards=[
            (date(2026, 10, 1), "in", D("10000000")),
            (date(2026, 10, 6), "in", D("2000000")),
            (date(2026, 10, 7), "in", D("3000000")),
            (date(2026, 10, 8), "out", D("1000000")),
            (date(2026, 10, 9), "out", D("1200000")),
            (date(2026, 10, 10), "out", D("100000")),  # pemakaian internal
        ],
        adjustments=[(date(2026, 10, 12), D("-50000"))],
        entries=[(date(2026, 10, 6), "pemasukan", D("700000")), (date(2026, 10, 7), "pengeluaran", D("250000"))],
        piutang_lain=[(date(2026, 10, 8), D("400000")), (date(2026, 10, 15), D("-100000"))],
    )
    for day in range(1, 20):
        p = position(data, date(2026, 10, day))
        aset = p["kas"] + p["stok"] + p["piutang"] + p["piutang_lainnya"]
        assert aset == p["utang"] + p["laba"], (day, p)
    end = position(data, date(2026, 10, 19))
    assert end["piutang"] == D("1500000") and end["utang"] == D("2000000")


def test_saldo_awal_oktober():
    data = Data(entries=[(date(2026, 9, 5), "pemasukan", D("999")), (date(2026, 10, 2), "pemasukan", D("100"))])
    p = position_with_opening(data, date(2026, 10, 3))
    assert p["kas"] == OPENING["kas"] + 100 and p["laba"] == D("100")
    assert OPENING["piutang"] == 0 and OPENING["piutang_lainnya"] == 0 and OPENING["kas"] == D("2124500")
    assert position_with_opening(data, OPENING_DATE)["piutang_lainnya"] == D("9200000")
    # Pelunasan 10 Okt atas penjualan piutang 20 Sep; kas bon 12 Okt.
    data.sales.append(Trade(date(2026, 9, 20), D("300"), "piutang", [(date(2026, 10, 10), D("300"))]))
    data.piutang_lain.append((date(2026, 10, 12), D("50")))
    q = position_with_opening(data, date(2026, 10, 12))
    assert q["piutang"] == OPENING["piutang"] - 300 and q["kas"] == OPENING["kas"] + 100 + 300 - 50
    assert q["piutang_lainnya"] == OPENING["piutang_lainnya"] + 50


def _row(dn, dc, cn, cc, amt="500"):
    return JournalRow("x", date(2026, 10, 10), "", D(amt), "d", dn, classify(dn, dc), "c", cn, classify(cn, cc))


def test_transaksi_umum_dipetakan_ke_neraca():
    assert is_piutang_lain("Penerimaan pembayaran  KASBON")
    assert not is_manual("TOKO:1", "x") and not is_manual("", "inventory_cogs") and is_manual("", "umum")
    lunas = _row("Kas Toko", "aset", "Piutang Lainnya", "aset")
    assert lunas.deltas() == {"kas": D("500"), "piutang_lainnya": D("-500")} and lunas.jenis == "mutasi"
    beban = _row("Beban Listrik", "beban", "Kas Toko", "aset")
    assert beban.jenis == "pengeluaran" and beban.deltas() == {"laba": D("-500"), "kas": D("-500")}
    assert _row("Kas", "aset", "Modal Disetor", "ekuitas").deltas() == {"kas": D("500"), "modal": D("500")}
    assert _row("Kas", "aset", "Prive", "ekuitas").deltas() == {}
    data = Data(journal=[(lunas.date, lunas.deltas()), (beban.date, beban.deltas()),
                         (date(2026, 10, 11), _row("Utang Usaha", "kewajiban", "Kas", "aset").deltas())])
    p = position_with_opening(data, date(2026, 10, 12))
    assert p["kas"] == OPENING["kas"] + 500 - 500 - 500 and p["piutang_lainnya"] == OPENING["piutang_lainnya"] - 500
    assert p["laba"] == D("-500")


def test_jurnal_mulai_september():
    lunas = {"kas": D("100"), "piutang_lainnya": D("-100")}
    data = Data(journal=[(date(2026, 8, 31), {"kas": D("999")}), (date(2026, 9, 10), lunas)])
    assert position_with_opening(data, date(2026, 9, 12))["kas"] == position_with_opening(Data(), date(2026, 9, 12))["kas"] + 100
    # Kas: transaksi menu Transaksi September diabaikan untuk minggu Oktober; akun lain tetap.
    q = position_with_opening(data, date(2026, 10, 3))
    assert q["kas"] == OPENING["kas"] and q["piutang_lainnya"] == OPENING["piutang_lainnya"] - 100


def test_rincian_kas_sama_dengan_neraca():
    from modules.siabumdes.toko.application.neraca import kas_breakdown
    data = Data(
        sales=[Trade(date(2026, 10, 6), D("700"), "cash"), Trade(date(2026, 9, 20), D("300"), "piutang", [(date(2026, 10, 7), D("300"))])],
        purchases=[Trade(date(2026, 10, 6), D("2000"), "cash"), Trade(date(2026, 9, 21), D("400"), "credit", [(date(2026, 10, 8), D("150"))])],
        entries=[(date(2026, 10, 6), "pemasukan", D("50")), (date(2026, 10, 7), "pengeluaran", D("20"))],
        piutang_lain=[(date(2026, 10, 8), D("-80")), (date(2026, 10, 9), D("10"))],
        journal=[(date(2026, 9, 15), {"kas": D("-999")}), (date(2026, 10, 9), {"kas": D("60"), "laba": D("60")}),
                 (date(2026, 10, 10), {"kas": D("-5"), "laba": D("-5")})],
    )
    for d in (date(2026, 9, 25), date(2026, 10, 12)):
        k = kas_breakdown(data, d)
        assert k["kas_akhir"] == position_with_opening(data, d)["kas"], (d, k)
    k = kas_breakdown(data, date(2026, 10, 12))
    assert k["kas_awal"] == OPENING["kas"] and k["masuk_pelunasan_kasbon"] == 80 and k["keluar_transaksi"] == 5


def test_modal_dan_laba_dari_transaksi():
    from modules.siabumdes.toko.application.neraca import laba_split, modal_at
    assert "stok" not in OPENING
    modal = _row("Kas", "aset", "Penyertaan Modal BUMDES", "ekuitas")
    assert modal.modal_delta == D("500") and modal.deltas() == {"kas": D("500"), "modal": D("500")}
    assert _row("Kas", "aset", "Saldo Laba", "ekuitas").modal_delta == 0
    data = Data(modal=[(date(2026, 8, 1), D("1000")), (modal.date, modal.modal_delta)],
                entries=[(date(2026, 9, 10), "pemasukan", D("7")), (date(2026, 10, 6), "pemasukan", D("100")),
                         (date(2026, 11, 3), "pemasukan", D("40"))])
    assert modal_at(data, date(2026, 9, 30)) == D("1000") and modal_at(data, date(2026, 10, 31)) == D("1500")
    assert laba_split(data, date(2026, 9, 27), date(2026, 9, 1)) == (0, 0)
    assert laba_split(data, date(2026, 10, 31), date(2026, 10, 1)) == (0, D("100"))
    assert laba_split(data, date(2026, 11, 8), date(2026, 11, 1)) == (D("100"), D("40"))


def test_modal_dan_belanja_modal_awal_september():
    from modules.siabumdes.toko.application.journal import belongs_to_unit5
    from modules.siabumdes.toko.application.neraca import kas_breakdown, modal_at
    assert belongs_to_unit5("UU05", "x", "y", set()) and not belongs_to_unit5("UU01", "1.1.01.15", "y", {"1.1.01.15"})
    assert belongs_to_unit5(None, "1.1.01.15", "3.1.01.01", {"1.1.01.15"}) and not belongs_to_unit5(None, "a", "b", {"c"})
    # Modal 30 jt (Debit Kas / Kredit Modal) 1 Sep; Belanja Modal Awal 9 jt (kartu stok + pembelian modal_awal) 2 Sep.
    data = Data(
        modal=[(date(2026, 9, 1), D("30000000"))],
        journal=[(date(2026, 9, 1), {"kas": D("30000000"), "modal": D("30000000")})],
        purchases=[Trade(date(2026, 9, 2), D("9000000"), "modal_awal")],
        cards=[(date(2026, 9, 2), "in", D("9000000")), (date(2026, 10, 6), "out", D("1000000"))],
    )
    sep = position_with_opening(data, date(2026, 9, 27))
    assert sep["kas"] == D("30000000") - D("9000000") - D("9200000") and sep["stok"] == D("9000000")
    assert kas_breakdown(data, date(2026, 9, 27))["kas_akhir"] == sep["kas"]
    okt = position_with_opening(data, date(2026, 10, 11))
    # Stok = nilai kartu stok kumulatif; Kas Oktober dari saldo awal (efek kas September sudah di dalamnya).
    assert okt["stok"] == D("8000000") and okt["kas"] == OPENING["kas"]
    assert modal_at(data, date(2026, 10, 11)) == D("30000000")


def test_buku_besar_pembanding():
    from modules.siabumdes.toko.application.neraca import ledger_at
    beli = _row("Persediaan Barang Dagang", "aset", "Kas Toko", "aset", "9000")
    assert beli.balance_deltas() == {"stok": D("9000"), "kas": D("-9000")}
    assert _row("Kas Toko", "aset", "Prive", "ekuitas").balance_deltas() == {"kas": D("500")}
    data = Data(ledger=[(beli.date, beli.balance_deltas())])
    assert ledger_at(data, date(2026, 10, 31))["stok"] == D("9000") and ledger_at(data, date(2026, 10, 1))["stok"] == 0
