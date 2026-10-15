"""Neraca toko harus seimbang: Kas + Stok + Piutang = Utang + Modal + Laba."""
from datetime import date
from decimal import Decimal as D

from modules.siabumdes.toko.application.neraca import MODAL, OPENING, OPENING_DATE, Data, Trade, position, position_with_opening


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
    )
    for day in range(1, 20):
        p = position(data, date(2026, 10, day))
        aset = p["kas"] + p["stok"] + p["piutang"]
        assert aset == p["utang"] + MODAL + p["laba"], (day, p)
    end = position(data, date(2026, 10, 19))
    assert end["piutang"] == D("1500000") and end["utang"] == D("2000000")


def test_saldo_awal_oktober():
    data = Data(entries=[(date(2026, 9, 5), "pemasukan", D("999")), (date(2026, 10, 2), "pemasukan", D("100"))])
    p = position_with_opening(data, date(2026, 10, 3))
    assert p["kas"] == OPENING["kas"] + 100 and p["laba"] == D("100")
    assert sum(OPENING.values()) == MODAL
    assert position_with_opening(data, OPENING_DATE) == position(data, OPENING_DATE)
    q = position_with_opening(data, date(2026, 10, 5))
    assert q["piutang"] == 0 and q["kas"] == OPENING["kas"] + OPENING["piutang"] + 100
