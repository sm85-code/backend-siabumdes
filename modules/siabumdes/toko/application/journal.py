"""Transaksi umum (menu Transaksi) unit UU05 yang dibaca Perduitan #1/#2/#3.

Hanya transaksi manual: transaksi otomatis (Perduitan ``TOKO:``, stok/penjualan/
pembelian, bagi hasil, imbal hasil, jurnal penutup) dilewati supaya tidak dobel.
Setiap sisi (debit/kredit) dipetakan ke pos Neraca/Laba Rugi toko lewat akun COA.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from modules.siabumdes.infrastructure.models import Account, Transaction, UnitUsaha

UNIT_CODE = "UU05"
JOURNAL_START = date(2026, 9, 1)  # transaksi sebelum September 2026 diabaikan
AUTO_REF_PREFIXES = ("TOKO:", "YIELD:", "BAGIHASIL-", "stock-", "sale-", "purchase-")
AUTO_TYPES = ("inventory_", "purchase_payment", "sale_payment", "jurnal_penutup", "bagi_hasil", "imbal_hasil")


def is_manual(reference: str | None, transaction_type: str | None, is_closing: bool = False) -> bool:
    ref, typ = reference or "", transaction_type or ""
    return not is_closing and not ref.startswith(AUTO_REF_PREFIXES) and not typ.startswith(AUTO_TYPES)


def belongs_to_unit5(unit_code: str | None, debit: str, credit: str, unit5_codes: set[str]) -> bool:
    """Transaksi Unit 5: unitnya UU05, atau tanpa unit (BUMDes pusat) tetapi memakai akun COA UU05."""
    if unit_code:
        return unit_code == UNIT_CODE
    return debit in unit5_codes or credit in unit5_codes


def classify(name: str, category: str, subcategory: str = "") -> str | None:
    """Pos toko untuk satu akun: kas|piutang_lainnya|piutang|utang|stok|pendapatan|beban, atau None."""
    n, c, s = " ".join((name or "").lower().split()), (category or "").lower(), (subcategory or "").lower()
    if c == "pendapatan":
        return "pendapatan"
    if c in ("beban", "hpp"):
        return "beban"
    if c == "ekuitas":
        # Hanya penyertaan modal; saldo laba, bagi hasil, ikhtisar laba rugi bukan Modal.
        named = ("penyertaan modal" in n or n.startswith("modal")) and "bagi hasil" not in n
        return "modal" if s in MODAL_SUBCATEGORIES or (not s and named) else None
    if any(k in n for k in ("piutang lain", "kas bon", "kasbon", "pinjam")):
        return "piutang_lainnya"
    if s == "kas_bank" or n.startswith(("kas", "bank")):
        return "kas"
    if "piutang" in n:
        return "piutang"
    if c == "kewajiban" and "utang" in n and "bagi hasil" not in n:
        return "utang"
    if "persediaan" in n:
        return "stok"
    return None


MODAL_SUBCATEGORIES = ("modal_desa", "modal_masyarakat")

# Pengaruh sisi debit (+1) / kredit (-1) terhadap nilai pos di Neraca toko / laba.
SIGN = {"kas": 1, "piutang_lainnya": 1, "piutang": 1, "stok": 1, "utang": -1, "pendapatan": -1, "beban": 1, "modal": -1}


@dataclass
class JournalRow:
    id: str
    date: date
    description: str
    amount: Decimal
    debit_code: str
    debit_name: str
    debit_pos: str | None
    credit_code: str
    credit_name: str
    credit_pos: str | None
    reference: str = ""
    transaction_type: str = ""
    is_closing: bool = False

    @property
    def counted(self) -> bool:
        return bool(self.debit_pos and self.credit_pos)

    @property
    def jenis(self) -> str:
        """pemasukan/pengeluaran untuk Perduitan #1 (laba), selain itu 'mutasi' (Neraca saja)."""
        if "pendapatan" in (self.debit_pos, self.credit_pos):
            return "pemasukan" if self.credit_pos == "pendapatan" else "pengeluaran"
        if "beban" in (self.debit_pos, self.credit_pos):
            return "pengeluaran" if self.debit_pos == "beban" else "pemasukan"
        return "mutasi"

    @property
    def modal_delta(self) -> Decimal:
        """Perubahan Modal BUMDes: kredit akun modal menambah, debit mengurangi."""
        if (self.debit_pos == "modal") == (self.credit_pos == "modal"):
            return Decimal(0)
        return self.amount if self.credit_pos == "modal" else -self.amount

    def balance_deltas(self) -> dict[str, Decimal]:
        """Saldo buku besar per pos Neraca (per sisi, tanpa syarat kedua akun dikenali)."""
        out: dict[str, Decimal] = {}
        for pos, side in ((self.debit_pos, 1), (self.credit_pos, -1)):
            if pos and pos not in ("pendapatan", "beban"):
                out[pos] = out.get(pos, Decimal(0)) + side * SIGN[pos] * self.amount
        return out

    def deltas(self) -> dict[str, Decimal]:
        """Perubahan pos Neraca; 'laba' = pendapatan - beban. Kosong bila ada akun yang tak dikenali."""
        if not self.counted:
            return {}
        out: dict[str, Decimal] = {}
        for pos, side in ((self.debit_pos, 1), (self.credit_pos, -1)):
            if pos in ("pendapatan", "beban"):
                out["laba"] = out.get("laba", Decimal(0)) - side * self.amount
            else:
                out[pos] = out.get(pos, Decimal(0)) + side * SIGN[pos] * self.amount
        return out

    def out(self) -> dict:
        return {
            "id": self.id, "tanggal": self.date.isoformat(), "keterangan": self.description, "reference": self.reference,
            "nominal": str(self.amount), "jenis": self.jenis, "counted": self.counted,
            "debit": {"code": self.debit_code, "name": self.debit_name, "pos": self.debit_pos},
            "credit": {"code": self.credit_code, "name": self.credit_name, "pos": self.credit_pos},
            "deltas": {k: str(v) for k, v in self.deltas().items()},
        }


async def load_journal(
    session, start: date | None = None, end: date | None = None, clamp: bool = True, manual_only: bool = True,
) -> list[JournalRow]:
    """clamp=False: semua tanggal (Modal BUMDes). manual_only=False: termasuk transaksi otomatis
    (buku besar lengkap, untuk pembanding Neraca)."""
    accounts: dict[str, Account] = {}
    for a in (await session.scalars(select(Account))).all():
        if a.code not in accounts or a.group_code == UNIT_CODE:
            accounts[a.code] = a
    unit5 = {c for c, a in accounts.items() if a.group_code == UNIT_CODE}
    q = (select(Transaction, UnitUsaha.code).outerjoin(UnitUsaha, Transaction.unit_usaha_id == UnitUsaha.id)
         .order_by(Transaction.date, Transaction.created_at))
    if clamp:
        start = max(start or JOURNAL_START, JOURNAL_START)
    if start:
        q = q.where(Transaction.date >= start)
    if end:
        q = q.where(Transaction.date <= end)
    rows = []
    for t, unit_code in (await session.execute(q)).all():
        if not belongs_to_unit5(unit_code, t.debit_account_code, t.credit_account_code, unit5):
            continue
        if manual_only and not is_manual(t.reference, t.transaction_type, t.is_closing):
            continue
        d, c = accounts.get(t.debit_account_code), accounts.get(t.credit_account_code)
        rows.append(JournalRow(
            t.id, t.date, t.description or "", Decimal(t.amount),
            t.debit_account_code, d.name if d else "", classify(d.name, d.category, d.subcategory) if d else None,
            t.credit_account_code, c.name if c else "", classify(c.name, c.category, c.subcategory) if c else None,
            t.reference or "", t.transaction_type or "", bool(t.is_closing),
        ))
    return rows
