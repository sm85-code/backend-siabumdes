"""Neraca Manajemen Toko (Unit 5): posisi kumulatif per tanggal.

Definisi dibuat saling konsisten supaya Aset = Kewajiban + Ekuitas:
- Kas/Bank = modal + kas masuk - kas keluar s.d. tanggal.
- Stok = nilai kartu stok (masuk - keluar, nilai HPP saat mutasi) + penyesuaian.
- Piutang = penjualan piutang - pelunasannya; Utang = pembelian kredit - pelunasannya.
- Laba = pemasukan toko + pendapatan penjualan - pengeluaran toko - HPP barang keluar
  (penjualan & pemakaian internal) +/- penyesuaian stok.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from modules.siabumdes.inventory.infrastructure.models import Product, Purchase, Sale, StockAdjustment, StockCard
from modules.siabumdes.toko.application.journal import load_journal
from modules.siabumdes.toko.infrastructure.models import TokoEntry, TokoItem

MODAL = Decimal("30000000")
ZERO = Decimal("0")

# Saldo awal riil per akhir September 2026 (setelah bagi hasil). Untuk tanggal
# setelah OPENING_DATE: posisi = saldo awal + mutasi setelah OPENING_DATE;
# laba = akumulasi sejak 1 Oktober 2026. Tanggal s.d. OPENING_DATE tetap seperti dulu.
OPENING_DATE = date(2026, 9, 30)
OPENING = {
    "kas": Decimal("2124500"),
    "piutang": ZERO,  # saldo awal piutang 8.392.000 diabaikan (permintaan pengguna)
    "piutang_lainnya": ZERO,  # saldo awal 9.700.000 diabaikan (permintaan pengguna)
    "stok": Decimal("9783500"),
    "utang": ZERO,
}
PIUTANG_LAIN_LAMA = Decimal("9200000")
# Perduitan membaca transaksi menu Transaksi mulai tanggal ini; sebelumnya diabaikan.
JOURNAL_START = date(2026, 9, 1)
# Khusus Kas: untuk tanggal setelah OPENING_DATE, transaksi menu Transaksi September diabaikan
# (kas September sudah tercermin di saldo awal Kas 30 Sep).
KAS_JOURNAL_START_AFTER_OPENING = date(2026, 10, 1)


@dataclass
class Trade:
    date: date
    total: Decimal
    method: str  # sale: cash|piutang ; purchase: cash|credit|modal_awal
    payments: list[tuple[date, Decimal]] = field(default_factory=list)


@dataclass
class Data:
    sales: list[Trade] = field(default_factory=list)
    purchases: list[Trade] = field(default_factory=list)
    cards: list[tuple[date, str, Decimal]] = field(default_factory=list)  # (tanggal, in|out, nilai)
    adjustments: list[tuple[date, Decimal]] = field(default_factory=list)  # (tanggal, nilai +/-)
    entries: list[tuple[date, str, Decimal]] = field(default_factory=list)  # (tanggal, pemasukan|pengeluaran, nominal)
    # Kas bon / pinjaman / piutang lainnya: (tanggal, +keluar kas jadi piutang, -pelunasan).
    piutang_lain: list[tuple[date, Decimal]] = field(default_factory=list)
    # Transaksi manual menu Transaksi: (tanggal, {pos: perubahan}) pos = kas|piutang|piutang_lainnya|utang|stok|laba.
    journal: list[tuple[date, dict[str, Decimal]]] = field(default_factory=list)


# Item Perduitan yang namanya memuat kata ini dicatat sebagai Piutang Lainnya (bukan laba).
PIUTANG_LAIN_KEYS = ("kas bon", "kasbon", "piutang lain", "pinjam")


def is_piutang_lain(name: str) -> bool:
    n = " ".join((name or "").lower().split())
    return any(k in n for k in PIUTANG_LAIN_KEYS)


def _paid(t: Trade, d: date, credit: str) -> Decimal:
    if t.date > d:
        return ZERO
    if t.method != credit:
        return t.total
    return sum((a for pd, a in t.payments if pd <= d), ZERO)


def position(data: Data, d: date) -> dict[str, Decimal]:
    toko_in = sum((n for t, j, n in data.entries if t <= d and j == "pemasukan"), ZERO)
    toko_out = sum((n for t, j, n in data.entries if t <= d and j != "pemasukan"), ZERO)
    revenue = sum((s.total for s in data.sales if s.date <= d), ZERO)
    cash_in = sum((_paid(s, d, "piutang") for s in data.sales), ZERO)
    cash_out = sum((_paid(p, d, "credit") for p in data.purchases), ZERO)
    piutang = sum((s.total for s in data.sales if s.date <= d and s.method == "piutang"), ZERO) - sum(
        (_paid(s, d, "piutang") for s in data.sales if s.method == "piutang"), ZERO)
    utang = sum((p.total for p in data.purchases if p.date <= d and p.method == "credit"), ZERO) - sum(
        (_paid(p, d, "credit") for p in data.purchases if p.method == "credit"), ZERO)
    stock_in = sum((v for t, k, v in data.cards if t <= d and k == "in"), ZERO)
    stock_out = sum((v for t, k, v in data.cards if t <= d and k == "out"), ZERO)
    adj = sum((v for t, v in data.adjustments if t <= d), ZERO)
    lain = sum((v for t, v in data.piutang_lain if t <= d), ZERO)
    return {
        "kas": MODAL + toko_in - toko_out + cash_in - cash_out - lain,
        "piutang_lainnya": lain,
        "stok": stock_in - stock_out + adj,
        "piutang": piutang,
        "utang": utang,
        "laba": toko_in + revenue - toko_out - stock_out + adj,
    }


def journal_sum(data: Data, d: date, start: date = JOURNAL_START) -> dict[str, Decimal]:
    return {k: sum((x.get(k, ZERO) for t, x in data.journal if start <= t <= d), ZERO)
            for k in ("kas", "piutang", "piutang_lainnya", "utang", "stok", "laba")}


def position_with_opening(data: Data, d: date) -> dict[str, Decimal]:
    """Transaksi menu Transaksi dihitung sejak JOURNAL_START (1 Sep 2026) dan tidak
    termasuk dalam saldo awal 30 Sep: posisi = saldo awal/legacy + jurnal s.d. tanggal.
    Laba dari jurnal setelah saldo awal hanya dihitung sejak 1 Okt (laba September sudah lewat)."""
    j = journal_sum(data, d)
    if d > OPENING_DATE:
        j["kas"] = journal_sum(data, d, KAS_JOURNAL_START_AFTER_OPENING)["kas"]
    out = _base_position(data, d)
    for k in ("kas", "piutang", "piutang_lainnya", "utang", "stok"):
        out[k] += j[k]
    out["laba"] += j["laba"] if d <= OPENING_DATE else journal_sum(data, d, date(2026, 10, 1))["laba"]
    return out


def _base_position(data: Data, d: date) -> dict[str, Decimal]:
    p = position(data, d)
    if d <= OPENING_DATE:
        # Sebelum saldo awal: Piutang Lainnya lama 9,2 jt dipinjamkan dari kas.
        p["kas"] -= PIUTANG_LAIN_LAMA
        p["piutang_lainnya"] += PIUTANG_LAIN_LAMA
        return p
    base = position(data, OPENING_DATE)
    out = {k: OPENING[k] + p[k] - base[k] for k in ("kas", "stok", "piutang", "utang", "piutang_lainnya")}
    out["laba"] = p["laba"] - base["laba"]
    return out


def _between(t: date, start: date | None, d: date) -> bool:
    return (start is None or t > start) and t <= d


def kas_breakdown(data: Data, d: date) -> dict[str, Decimal]:
    """Rincian pergerakan Kas s.d. tanggal d; kas_akhir sama dengan Kas di Neraca.

    Setelah OPENING_DATE: dari saldo awal Kas 30 Sep. Sampai OPENING_DATE: dari
    Modal BUMDes dikurangi Piutang Lainnya lama (perhitungan lama)."""
    after = d > OPENING_DATE
    start = OPENING_DATE if after else None
    jstart = KAS_JOURNAL_START_AFTER_OPENING if after else JOURNAL_START
    rng = lambda t: _between(t, start, d)  # noqa: E731
    pay = lambda trades, method: sum((a for x in trades if x.method == method for t, a in x.payments if rng(t)), ZERO)  # noqa: E731
    jk = [x.get("kas", ZERO) for t, x in data.journal if jstart <= t <= d]
    out = {
        "kas_awal": OPENING["kas"] if after else MODAL - PIUTANG_LAIN_LAMA,
        "masuk_perduitan": sum((n for t, j, n in data.entries if rng(t) and j == "pemasukan"), ZERO),
        "masuk_penjualan_tunai": sum((x.total for x in data.sales if rng(x.date) and x.method != "piutang"), ZERO),
        "masuk_pelunasan_piutang": pay(data.sales, "piutang"),
        "masuk_pelunasan_kasbon": -sum((v for t, v in data.piutang_lain if rng(t) and v < 0), ZERO),
        "masuk_transaksi": sum((v for v in jk if v > 0), ZERO),
        "keluar_perduitan": sum((n for t, j, n in data.entries if rng(t) and j != "pemasukan"), ZERO),
        "keluar_pembelian_tunai": sum((x.total for x in data.purchases if rng(x.date) and x.method != "credit"), ZERO),
        "keluar_pelunasan_utang": pay(data.purchases, "credit"),
        "keluar_kasbon": sum((v for t, v in data.piutang_lain if rng(t) and v > 0), ZERO),
        "keluar_transaksi": -sum((v for v in jk if v < 0), ZERO),
    }
    masuk = sum((v for k, v in out.items() if k.startswith("masuk_")), ZERO)
    keluar = sum((v for k, v in out.items() if k.startswith("keluar_")), ZERO)
    out["kas_akhir"] = out["kas_awal"] + masuk - keluar
    return out


async def load(session) -> Data:
    data = Data()
    for s, d in (await session.execute(
        select(Sale, StockCard.movement_date).join(StockCard, Sale.stock_card_id == StockCard.id)
        .options(selectinload(Sale.payments))
    )).all():
        data.sales.append(Trade(d, Decimal(s.total_amount), s.payment_method, [(p.paid_date, Decimal(p.amount)) for p in s.payments]))
    for p, d in (await session.execute(
        select(Purchase, StockCard.movement_date).join(StockCard, Purchase.stock_card_id == StockCard.id)
        .options(selectinload(Purchase.payments))
    )).all():
        data.purchases.append(Trade(d, Decimal(p.total_amount), p.payment_method, [(x.paid_date, Decimal(x.amount)) for x in p.payments]))
    for c in (await session.scalars(select(StockCard).where(StockCard.finance_status != "cancelled"))).all():
        data.cards.append((c.movement_date, c.direction, Decimal(c.total_value)))
    for a, cost in (await session.execute(
        select(StockAdjustment, Product.cost_price).join(Product, StockAdjustment.product_id == Product.id)
    )).all():
        # Nilai penyesuaian tidak disimpan; pakai HPP produk (sama seperti jurnal penyesuaian).
        data.adjustments.append((a.adjustment_date, Decimal(cost) * a.quantity_delta))
    for e, jenis, name in (await session.execute(
        select(TokoEntry, TokoItem.jenis, TokoItem.name).join(TokoItem, TokoEntry.item_id == TokoItem.id)
    )).all():
        if is_piutang_lain(name):
            data.piutang_lain.append((e.tanggal, Decimal(e.nominal) * (-1 if jenis == "pemasukan" else 1)))
        else:
            data.entries.append((e.tanggal, jenis, Decimal(e.nominal)))
    # Transaksi manual dari menu Transaksi (unit UU05), dipetakan lewat akun COA.
    for row in await load_journal(session, start=JOURNAL_START):
        if row.deltas():
            data.journal.append((row.date, row.deltas()))
    return data
