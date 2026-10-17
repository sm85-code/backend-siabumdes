"""Perduitan #3 (Laba Rugi mingguan + Neraca) dari menu Transaksi: semua jurnal Unit 5.

Sumber satu-satunya = transaksi Unit 5 (otomatis + manual, termasuk transaksi tanpa unit yang
memakai akun COA UU05; lihat journal.load_journal). Jurnal penutup tidak dihitung. Bagi hasil dan
imbal hasil adalah pembagian laba, bukan pendapatan/beban usaha, jadi tidak masuk Laba Rugi.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from modules.siabumdes.toko.application.journal import JOURNAL_START, JournalRow
from modules.siabumdes.toko.application.weeks import first_monday, period_range, trade_week

ZERO = Decimal(0)
LR_ROWS = ("saldo", "jual", "lain", "trx_in", "umum", "hpp", "internal", "trx_out")
LR_IN = ("saldo", "jual", "lain", "trx_in")
BAL_KEYS = ("kas", "stok", "piutang", "piutang_lainnya", "utang", "modal")
WEEK_KEYS = ("w1", "w2", "w3", "w4", "w5")
# Saldo awal Kas yang ditetapkan pengguna (30 Sep 2026). Kas di Neraca sesudah tanggal ini =
# 2.124.500 + pergerakan akun Kas/Bank Unit 5 mulai 1 Okt 2026 (jurnal Kas September diabaikan).
KAS_OPENING_DATE = date(2026, 9, 30)
KAS_OPENING = Decimal("2124500")


def is_closing(r: JournalRow) -> bool:
    return r.is_closing or r.transaction_type.startswith("jurnal_penutup")


def is_profit_distribution(r: JournalRow) -> bool:
    return r.transaction_type.startswith(("bagi_hasil", "imbal_hasil")) or r.reference.startswith(("YIELD:", "BAGIHASIL-"))


def pl_lines(r: JournalRow, lainnya: set[str]) -> list[tuple[str, Decimal]]:
    """(baris Laba Rugi, nilai) untuk sisi akun Pendapatan/Beban/HPP satu jurnal."""
    if is_closing(r) or is_profit_distribution(r):
        return []
    toko = r.reference.startswith("TOKO:")
    out = []
    for pos, side in ((r.debit_pos, 1), (r.credit_pos, -1)):
        if pos == "pendapatan":
            row = ("lain" if r.reference[5:] in lainnya else "saldo") if toko else \
                "jual" if r.reference.startswith("stock-out-rev:") else "trx_in"
            out.append((row, -side * r.amount))
        elif pos == "beban":
            row = "umum" if toko else "hpp" if r.transaction_type == "inventory_cogs" else \
                "internal" if r.transaction_type == "inventory_internal_use" else "trx_out"
            out.append((row, side * r.amount))
    return out


def laba_delta(r: JournalRow, lainnya: set[str] = frozenset()) -> Decimal:
    return sum((v if row in LR_IN else -v for row, v in pl_lines(r, lainnya)), ZERO)


def weekly_laba_rugi(rows: list[JournalRow], period: str, lainnya: set[str]) -> dict[str, dict[str, Decimal]]:
    """{baris: {w1..w5, jumlah}} untuk minggu Senin–Sabtu periode (Minggu ikut minggu sebelumnya)."""
    out = {k: {w: ZERO for w in (*WEEK_KEYS, "jumlah")} for k in LR_ROWS}
    start, end = period_range(period)
    for r in rows:
        if not (start <= r.date <= end) or r.date < JOURNAL_START:
            continue
        p, w = trade_week(r.date)
        if p != period:
            continue
        for row, v in pl_lines(r, lainnya):
            out[row][f"w{w}"] += v
            out[row]["jumlah"] += v
    return out


def balances_at(rows: list[JournalRow], d: date) -> dict[str, Decimal]:
    """Saldo akun Unit 5 s.d. d per pos Neraca (tanpa jurnal penutup)."""
    out = {k: ZERO for k in BAL_KEYS}
    for r in rows:
        if r.date <= d and not is_closing(r):
            for k, v in r.balance_deltas().items():
                out[k] = out.get(k, ZERO) + v
    return out


def kas_at(rows: list[JournalRow], d: date) -> Decimal:
    """Kas/Bank di Neraca: s.d. 30 Sep 2026 saldo buku besar; sesudahnya 2.124.500 + pergerakan Kas sejak 1 Okt."""
    if d < KAS_OPENING_DATE:
        return balances_at(rows, d)["kas"]
    return KAS_OPENING + sum((r.balance_deltas().get("kas", ZERO) for r in rows
                              if KAS_OPENING_DATE < r.date <= d and not is_closing(r)), ZERO)


def laba_at(rows: list[JournalRow], d: date, after: date | None = None) -> Decimal:
    return sum((laba_delta(r) for r in rows if r.date <= d and (after is None or r.date > after)), ZERO)


def _other(r: JournalRow, pos: str) -> str | None:
    return r.credit_pos if r.debit_pos == pos else r.debit_pos


def kas_detail(rows: list[JournalRow], start: date, d: date) -> dict[str, Decimal | date]:
    """Pergerakan Kas start..d dari jurnal; kas_awal + masuk − keluar = kas_akhir = Kas di Neraca."""
    awal = start - timedelta(days=1)
    if awal < KAS_OPENING_DATE <= d:
        awal = KAS_OPENING_DATE  # periode melewati saldo awal: mulai dari 2.124.500 per 30 Sep
    out = {"kas_awal": kas_at(rows, awal), "kas_awal_date": awal}
    for r in rows:
        if not (awal < r.date <= d) or is_closing(r):
            continue
        v = r.balance_deltas().get("kas", ZERO)
        if not v:
            continue
        ref = r.reference
        cat = "kasbon" if _other(r, "kas") == "piutang_lainnya" else "perduitan" if ref.startswith("TOKO:") else \
            "penjualan_tunai" if ref.startswith("stock-out-rev:") else "pelunasan_piutang" if ref.startswith("sale-pay:") else \
            "pembelian_tunai" if ref.startswith("stock-in:") else "pelunasan_utang" if ref.startswith("purchase-pay:") else "transaksi"
        if cat == "kasbon":
            key = "masuk_pelunasan_kasbon" if v > 0 else "keluar_kasbon"
        else:
            key = f"{'masuk' if v > 0 else 'keluar'}_{cat}"
        out[key] = out.get(key, ZERO) + abs(v)
    out["kas_akhir"] = kas_at(rows, d)
    return out


def stok_detail(rows: list[JournalRow], start: date, d: date, modal_awal_heads: set[str]) -> dict[str, Decimal]:
    """Pergerakan akun Persediaan start..d dari jurnal (bertanda); stok_akhir = Stok di Neraca."""
    awal = start - timedelta(days=1)
    out = {"stok_awal": balances_at(rows, awal)["stok"]}
    for r in rows:
        if not (awal < r.date <= d) or is_closing(r):
            continue
        v = r.balance_deltas().get("stok", ZERO)
        if not v:
            continue
        ref = r.reference
        key = ("masuk_modal_awal" if ref.split(":", 1)[1] in modal_awal_heads else "masuk_reguler") if ref.startswith("stock-in:") else \
            "keluar_penjualan" if r.transaction_type == "inventory_cogs" else \
            "keluar_internal" if r.transaction_type == "inventory_internal_use" else \
            "penyesuaian" if ref.startswith("stock-adj:") else "transaksi"
        out[key] = out.get(key, ZERO) + v
    out["stok_akhir"] = balances_at(rows, d)["stok"]
    return out


def position(rows: list[JournalRow], d: date, period: str) -> dict[str, Decimal]:
    """Neraca per tanggal d: saldo akun Unit 5; Laba/Rugi Periode Lalu = s.d. sehari sebelum
    Senin Minggu ke-1 periode, Periode Berjalan = sejak Senin Minggu ke-1 s.d. d."""
    start = first_monday(period) - timedelta(days=1)
    # Saldo awal laba ditahan per 30 Sep 2026 = 0 (sejalan saldo awal Kas 2.124.500): sesudah tanggal itu
    # laba hanya dihitung sejak 1 Okt 2026.
    since = KAS_OPENING_DATE if d > KAS_OPENING_DATE else None
    berjalan_after = max(start, since) if since else start
    return {**balances_at(rows, d), "kas": kas_at(rows, d),
            "laba_lalu": laba_at(rows, min(start, d), after=since) - distributed_at(rows, d, after=since),
            "laba_berjalan": laba_at(rows, d, after=berjalan_after) if d > berjalan_after else ZERO}


def distributed_at(rows: list[JournalRow], d: date, after: date | None = None) -> Decimal:
    """Laba yang sudah dibagikan (bagi hasil/imbal hasil) s.d. d, dari sisi pos Neraca jurnalnya.

    Pembagian laba bukan beban (tidak masuk Laba Rugi), tetapi mengurangi saldo laba: kas yang keluar
    untuk bagi hasil harus mengurangi Laba/Rugi Periode Lalu, kalau tidak Neraca selisih sebesar
    bagi hasil yang dibayar. Nilai = penurunan (aset − utang − modal) akibat jurnal tsb."""
    out = ZERO
    for r in rows:
        if r.date <= d and (after is None or r.date > after) and not is_closing(r) and is_profit_distribution(r):
            for k, v in r.balance_deltas().items():
                out -= v if k in ("kas", "stok", "piutang", "piutang_lainnya") else -v
    return out
