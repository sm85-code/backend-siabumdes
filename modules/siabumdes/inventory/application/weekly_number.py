"""Minggu toko (Senin–Sabtu; Minggu ikut minggu sebelumnya) dan nomor dokumen mingguan."""

from __future__ import annotations

import re
from datetime import date, timedelta

BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
         "September", "Oktober", "November", "Desember"]
ROMAWI = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


def week_of(d: date) -> tuple[date, int]:
    """(Senin minggu itu, nomor minggu dalam bulan Senin tersebut)."""
    monday = d - timedelta(days=d.weekday())
    return monday, (monday.day - 1) // 7 + 1


def week_label(d: date) -> str:
    monday, n = week_of(d)
    sat = monday + timedelta(days=5)
    return f"Minggu {n} {BULAN[monday.month - 1]} {monday.year} ({monday:%d/%m/%Y} s.d. {sat:%d/%m/%Y})"


def partner_code(name: str) -> str:
    """Kode mitra dari nama: inisial (>=2 kata) atau huruf awal + konsonan (1 kata), 3 huruf."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    if not words:
        return "XXX"
    if len(words) >= 2:
        code = "".join(w[0] for w in words)[:3]
    else:
        w = words[0]
        code = (w[0] + re.sub(r"[aeiouAEIOU]", "", w[1:]))[:3]
        if len(code) < 3:
            code = w[:3]
    return code.upper()


def doc_number(prefix: str, name: str, d: date, seq: int, code: str | None = None) -> str:
    monday, n = week_of(d)
    return f"{prefix}-{(code or '').strip().upper() or partner_code(name)}-M{n}-{seq:03d}/{ROMAWI[monday.month - 1]}/{monday.year}"


async def resolve_code(session, partner_model, row) -> str:
    """Kode mitra dari field Kode (Manajemen Stok). Jika kosong, pakai Kode mitra lain
    bernama sama di unit yang sama; baru terakhir turunan nama."""
    from sqlalchemy import func, select

    if (row.code or "").strip():
        return row.code.strip().upper()
    other = await session.scalar(
        select(partner_model.code).where(
            partner_model.unit_usaha_id == row.unit_usaha_id,
            func.lower(func.trim(partner_model.name)) == (row.name or "").strip().lower(),
            partner_model.code.is_not(None), partner_model.code != "",
        ).limit(1)
    )
    return (other or "").strip().upper() or partner_code(row.name)


SEQ_RE = re.compile(r"-M\d+-(\d{3,})/")


async def weekly_meta(session, kind: str, unit_id: str, partner_id: str, d: date,
                      source_ids: list[str] | None = None) -> dict:
    """Nomor, tanggal dokumen (Sabtu) dan label periode untuk dokumen minggu tanggal d.

    Urutan = jumlah dokumen INV/PO terbit di minggu itu + 1 (mitra sama dapat nomor berikutnya).
    Jika transaksi sumber sudah masuk dokumen terbit, nomornya dipakai ulang.
    """
    from sqlalchemy import select

    from modules.siabumdes.inventory.infrastructure.models import (
        CommercialDocument, Customer, DocumentSource, Vendor,
    )

    partner_model = Customer if kind == "invoice" else Vendor
    prefix = "INV" if kind == "invoice" else "PO"
    monday, n = week_of(d)
    row = await session.get(partner_model, partner_id)
    code = await resolve_code(session, partner_model, row)
    pattern = f"{prefix}-%-M{n}-%/{ROMAWI[monday.month - 1]}/{monday.year}"
    number = None
    if source_ids:
        number = await session.scalar(
            select(CommercialDocument.number).join(DocumentSource, DocumentSource.document_id == CommercialDocument.id)
            .where(DocumentSource.source_id.in_(source_ids), CommercialDocument.kind == kind,
                   CommercialDocument.status != "void", CommercialDocument.number.like(pattern)).limit(1)
        )
    if not number:
        used = (await session.scalars(
            select(CommercialDocument.number).where(CommercialDocument.number.like(pattern)))).all()
        seq = max((int(m.group(1)) for u in used if (m := SEQ_RE.search(u or ""))), default=0) + 1
        number = doc_number(prefix, row.name, d, seq, code)
    return {
        "number": number,
        "issued_date": (monday + timedelta(days=5)).isoformat(),
        "period_label": week_label(d),
    }
