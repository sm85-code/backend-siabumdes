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


VOWELS = set("AEIOU")


def partner_code(name: str) -> str:
    """Kode mitra otomatis (3 huruf): huruf pertama nama + konsonan awal suku kata berikutnya
    (konsonan yang diikuti vokal), mis. Mandalawangi -> MDL, Bumi Lestari -> BML."""
    letters = re.sub(r"[^A-Z]", "", (name or "").upper())
    if not letters:
        return "XXX"
    code = letters[0]
    for i in range(1, len(letters) - 1):
        if len(code) == 3:
            break
        if letters[i] not in VOWELS and letters[i + 1] in VOWELS:
            code += letters[i]
    for ch in letters[1:]:  # kurang dari 3: tambah konsonan lain, lalu huruf apa saja
        if len(code) == 3:
            break
        if ch not in VOWELS and ch not in code[1:]:
            code += ch
    return (code + letters[len(code):])[:3].ljust(3, "X")


async def ensure_code(session, partner_model, row) -> str:
    """Kode tetap mitra: dibuat sistem sekali (unik per unit per jenis, bentrok -> digit) lalu disimpan."""
    from sqlalchemy import select

    if (row.code or "").strip():
        return row.code
    base = partner_code(row.name)
    used = set((await session.scalars(
        select(partner_model.code).where(partner_model.unit_usaha_id == row.unit_usaha_id, partner_model.code.is_not(None))
    )).all())
    code, n = base, 1
    while code in used:
        n += 1
        code = f"{base}{n}"
    row.code = code
    await session.flush()
    return code


def doc_number(prefix: str, name: str, d: date, seq: int, code: str | None = None) -> str:
    monday, n = week_of(d)
    return f"{prefix}-{(code or '').strip().upper() or partner_code(name)}-M{n}-{seq:03d}/{ROMAWI[monday.month - 1]}/{monday.year}"


async def resolve_code(session, partner_model, row) -> str:
    """Kode mitra = kode otomatis yang tampil di Data Orang-Orang."""
    return await ensure_code(session, partner_model, row)


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
