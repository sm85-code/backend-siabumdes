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

from modules.siabumdes.infrastructure.models import Account, Transaction, UnitUsaha
from modules.siabumdes.inventory.infrastructure.models import Product, Purchase, Sale, StockAdjustment, StockCard
from modules.siabumdes.toko.infrastructure.models import TokoEntry, TokoItem

MODAL = Decimal("30000000")
ZERO = Decimal("0")

# Saldo awal riil per akhir September 2026 (setelah bagi hasil). Untuk tanggal
# setelah OPENING_DATE: posisi = saldo awal + mutasi setelah OPENING_DATE;
# laba = akumulasi sejak 1 Oktober 2026. Tanggal s.d. OPENING_DATE tetap seperti dulu.
OPENING_DATE = date(2026, 9, 30)
OPENING = {
    "kas": Decimal("2124500"),
    "piutang": Decimal("8392000"),
    "piutang_lainnya": Decimal("9700000"),  # termasuk kas bon tukang 500.000
    "stok": Decimal("9783500"),
    "utang": ZERO,
}
PIUTANG_LAIN_LAMA = Decimal("9200000")


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


# Item Perduitan yang namanya memuat kata ini dicatat sebagai Piutang Lainnya (bukan laba).
PIUTANG_LAIN_KEYS = ("kas bon", "kasbon", "piutang lain", "pinjam")


def is_piutang_lain(name: str) -> bool:
    n = " ".join((name or "").lower().split())
    return any(k in n for k in PIUTANG_LAIN_KEYS)


def journal_piutang_lain(amount: Decimal, debit_name: str, credit_name: str) -> Decimal:
    """Transaksi umum yang mengenai akun Piutang Lainnya: debit menambah, kredit mengurangi."""
    d, c = is_piutang_lain(debit_name), is_piutang_lain(credit_name)
    if d == c:
        return ZERO
    return amount if d else -amount


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


def position_with_opening(data: Data, d: date) -> dict[str, Decimal]:
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
    # Transaksi umum (menu Transaksi) unit UU05 yang mendebit/mengkredit akun Piutang Lainnya,
    # mis. Debit Kas / Kredit Piutang Lainnya = pelunasan kas bon. Transaksi otomatis
    # dari Perduitan (reference TOKO:...) dilewati supaya tidak terhitung dua kali.
    names: dict[str, str] = {}
    for code, name in (await session.execute(select(Account.code, Account.name))).all():
        names.setdefault(code, name)
    for t in (await session.scalars(
        select(Transaction).join(UnitUsaha, Transaction.unit_usaha_id == UnitUsaha.id)
        .where(UnitUsaha.code == "UU05", Transaction.is_closing.is_(False))
    )).all():
        if (t.reference or "").startswith("TOKO:"):
            continue
        v = journal_piutang_lain(Decimal(t.amount), names.get(t.debit_account_code, ""), names.get(t.credit_account_code, ""))
        if v:
            data.piutang_lain.append((t.date, v))
    return data
