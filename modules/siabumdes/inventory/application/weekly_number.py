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


def in_week(tx_date: date, any_day: date) -> bool:
    """Transaksi tanggal tx_date termasuk minggu any_day (Senin–Minggu)."""
    return week_of(tx_date)[0] == week_of(any_day)[0]


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
    """Kode mitra: field Kode -> Kode mitra lain bernama sama -> kode di nomor invoice
    transaksi mitra (tampil di Dokumen, mis. INV-BML-001) -> turunan nama."""
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
    if (other or "").strip():
        return other.strip().upper()
    # Kode yang sudah dipakai di nomor invoice/nota transaksi mitra (mis. INV-BML-001 -> BML).
    from modules.siabumdes.inventory.infrastructure.models import Customer, Purchase, Sale

    model, field = (Sale, Sale.customer_id) if partner_model is Customer else (Purchase, Purchase.vendor_id)
    numbers = (await session.scalars(
        select(model.invoice_number).where(field == row.id, model.invoice_number != "").order_by(model.created_at.desc()).limit(20)
    )).all()
    for n in numbers:
        if (c := code_from_invoice(n)):
            return c
    return partner_code(row.name)


INVOICE_CODE_RE = re.compile(r"^\s*(?:INV|PO|NOTA|SO|PI)[-/ ]([A-Za-z]{2,6})[-/ ]", re.I)


def code_from_invoice(number: str | None) -> str:
    m = INVOICE_CODE_RE.match(number or "")
    return m.group(1).upper() if m else ""


async def weekly_meta(session, kind: str, unit_id: str, partner_id: str, d: date,
                      source_ids: list[str] | None = None) -> dict:
    """Nomor, tanggal dokumen (Sabtu) dan label periode untuk dokumen minggu tanggal d.

    Urutan tetap per (unit, jenis, minggu, mitra): diberikan saat pertama dibutuhkan
    (maks minggu itu + 1) lalu dipakai ulang untuk unduhan/terbit berikutnya.
    """
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from modules.siabumdes.inventory.infrastructure.models import Customer, DocNumber, Vendor

    partner_model = Customer if kind == "invoice" else Vendor
    prefix = "INV" if kind == "invoice" else "PO"
    monday, _ = week_of(d)
    row = await session.get(partner_model, partner_id)
    code = await resolve_code(session, partner_model, row)
    key = (DocNumber.unit_usaha_id == unit_id, DocNumber.kind == kind, DocNumber.week_start == monday)
    seq = None
    for _attempt in range(5):
        seq = await session.scalar(select(DocNumber.seq).where(*key, DocNumber.partner_id == partner_id))
        if seq:
            break
        nxt = (await session.scalar(select(func.max(DocNumber.seq)).where(*key)) or 0) + 1
        try:
            async with session.begin_nested():
                session.add(DocNumber(unit_usaha_id=unit_id, kind=kind, week_start=monday, partner_id=partner_id, seq=nxt))
            seq = nxt
            break
        except IntegrityError:
            continue  # bentrok dengan permintaan lain: baca ulang
    if not seq:
        raise RuntimeError("Gagal menentukan nomor dokumen")
    number = doc_number(prefix, row.name, d, seq, code)
    return {
        "number": number,
        "issued_date": (monday + timedelta(days=5)).isoformat(),
        "period_label": week_label(d),
    }
