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


async def weekly_meta(session, kind: str, unit_id: str, partner_id: str, d: date) -> dict:
    """Nomor, tanggal dokumen (Sabtu) dan label periode untuk dokumen minggu tanggal d."""
    from sqlalchemy import select

    from modules.siabumdes.inventory.infrastructure.models import Customer, Purchase, Sale, StockCard, Vendor

    model, partner_model, field = (Sale, Customer, "customer_id") if kind == "invoice" else (Purchase, Vendor, "vendor_id")
    monday, _ = week_of(d)
    ids = (await session.execute(
        select(partner_model.id).distinct().join(model, getattr(model, field) == partner_model.id)
        .join(StockCard, model.stock_card_id == StockCard.id)
        .where(partner_model.unit_usaha_id == unit_id,
               StockCard.movement_date >= monday, StockCard.movement_date <= monday + timedelta(days=6))
    )).scalars().all()
    rows = {r.id: r for r in (await session.scalars(
        select(partner_model).where(partner_model.id.in_(list(ids) + [partner_id])))).all()}
    order = sorted(rows, key=lambda i: ((rows[i].name or "").lower(), i))
    p = rows[partner_id]
    return {
        "number": doc_number("INV" if kind == "invoice" else "PO", p.name, d, order.index(partner_id) + 1, p.code),
        "issued_date": (monday + timedelta(days=5)).isoformat(),
        "period_label": week_label(d),
    }
