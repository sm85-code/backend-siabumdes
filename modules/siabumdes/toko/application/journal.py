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


def classify(name: str, category: str, subcategory: str = "") -> str | None:
    """Pos toko untuk satu akun: kas|piutang_lainnya|piutang|utang|stok|pendapatan|beban, atau None."""
    n, c, s = " ".join((name or "").lower().split()), (category or "").lower(), (subcategory or "").lower()
    if c == "pendapatan":
        return "pendapatan"
    if c in ("beban", "hpp"):
        return "beban"
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


# Pengaruh sisi debit (+1) / kredit (-1) terhadap nilai pos di Neraca toko / laba.
SIGN = {"kas": 1, "piutang_lainnya": 1, "piutang": 1, "stok": 1, "utang": -1, "pendapatan": -1, "beban": 1}


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
            "id": self.id, "tanggal": self.date.isoformat(), "keterangan": self.description,
            "nominal": str(self.amount), "jenis": self.jenis, "counted": self.counted,
            "debit": {"code": self.debit_code, "name": self.debit_name, "pos": self.debit_pos},
            "credit": {"code": self.credit_code, "name": self.credit_name, "pos": self.credit_pos},
            "deltas": {k: str(v) for k, v in self.deltas().items()},
        }


async def load_journal(session, start: date | None = None, end: date | None = None) -> list[JournalRow]:
    accounts: dict[str, Account] = {}
    for a in (await session.scalars(select(Account))).all():
        if a.code not in accounts or a.group_code == UNIT_CODE:
            accounts[a.code] = a
    q = (select(Transaction).join(UnitUsaha, Transaction.unit_usaha_id == UnitUsaha.id)
         .where(UnitUsaha.code == UNIT_CODE).order_by(Transaction.date, Transaction.created_at))
    start = max(start or JOURNAL_START, JOURNAL_START)
    if start:
        q = q.where(Transaction.date >= start)
    if end:
        q = q.where(Transaction.date <= end)
    rows = []
    for t in (await session.scalars(q)).all():
        if not is_manual(t.reference, t.transaction_type, t.is_closing):
            continue
        d, c = accounts.get(t.debit_account_code), accounts.get(t.credit_account_code)
        rows.append(JournalRow(
            t.id, t.date, t.description or "", Decimal(t.amount),
            t.debit_account_code, d.name if d else "", classify(d.name, d.category, d.subcategory) if d else None,
            t.credit_account_code, c.name if c else "", classify(c.name, c.category, c.subcategory) if c else None,
        ))
    return rows
