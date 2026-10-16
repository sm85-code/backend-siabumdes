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


def doc_number(prefix: str, name: str, d: date, seq: int) -> str:
    monday, n = week_of(d)
    return f"{prefix}-{partner_code(name)}-M{n}-{seq:03d}/{ROMAWI[monday.month - 1]}/{monday.year}"
