"""Shared helpers for the PDF/Excel/Word report renderers.

Excel keeps real numeric cells with a number_format (so it stays sortable),
but PDF and Word only ever render text -- so money columns there must be
pre-formatted into Rupiah strings, matching the frontend's fmtRp().
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def is_money_header(h: str) -> bool:
    h = (h or "").lower()
    return any(
        k in h
        for k in (
            "nominal", "jumlah", "debit", "kredit", "saldo", "rp",
            "amount", "nilai", "laba", "beban", "pendapatan",
        )
    )


def row_is_total(row: list[Any]) -> bool:
    """True for a genuine total/summary line (short label + amount), never
    for a long free-text note. Without the length guard, a CaLK policy
    paragraph mentioning "Laba Bersih Operasional" in passing would get
    shaded and bolded like an actual total row."""
    cells = [str(c or "") for c in row]
    if any(len(c) > 60 for c in cells):
        return False
    joined = " ".join(c.lower() for c in cells)
    return any(k in joined for k in ("total", "jumlah", "laba bersih", "saldo akhir", "saldo awal"))


def looks_numeric(value: Any) -> bool:
    """True only for a value that is actually a number, not just a cell that
    happens to sit in a column whose header matches a money keyword. A CaLK
    column literally titled "Nilai" mixes real amounts with long free-text
    policy notes -- treating every cell in it as numeric (right-aligned,
    non-wrapping) makes those notes overflow off the page."""
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False
    return False


def format_money(value: Any) -> str:
    """Format a numeric amount as Rupiah text, e.g. -> 'Rp 1.000.000'.

    Non-numeric values (None, empty string, already-formatted text) pass
    through unchanged so section labels sharing a money column still render.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        try:
            n = float(stripped)
        except ValueError:
            return value
    elif isinstance(value, (int, float, Decimal)):
        n = float(value)
    else:
        return str(value)
    sign = "-" if n < 0 else ""
    return f"{sign}Rp {abs(round(n)):,}".replace(",", ".")


def format_money_row(headers: list[str], row: list[Any]) -> list[Any]:
    """Return a copy of `row` with money columns formatted as Rupiah text."""
    money_cols = {i for i, h in enumerate(headers) if is_money_header(h)}
    return [format_money(c) if i in money_cols else c for i, c in enumerate(row)]
