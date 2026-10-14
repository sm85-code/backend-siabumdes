"""Consistent user-facing descriptions; references remain machine identifiers."""

from __future__ import annotations

from decimal import Decimal
from typing import Union


def format_qty(quantity: Union[int, float, Decimal, str]) -> str:
    """Format quantity without trailing .0 (integer if whole, else trim zeros)."""
    if isinstance(quantity, bool):
        raise TypeError("quantity must be numeric")
    if isinstance(quantity, int):
        return str(quantity)
    d = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
    if d == d.to_integral_value():
        return str(int(d))
    text = format(d.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def belanja_modal_awal_description(*, sku: str, name: str, quantity, unit: str) -> str:
    """Keterangan transaksi/jurnal Belanja Modal Awal.

    Contoh: Belanja Modal Awal - [KRS.MM-001] Oyek - 10 kg
    """
    satuan = (unit or "").strip() or "pcs"
    return (
        f"Belanja Modal Awal - [{(sku or '').strip()}] {(name or '').strip()} "
        f"- {format_qty(quantity)} {satuan}"
    )


def inventory_description(action, *, partner=None, invoice=None, product=None, quantity=None, note=None):
    parts = [action]
    if partner:
        parts.append(partner.strip())
    if invoice:
        parts.append(invoice.strip())
    if product:
        parts.append(f"{product.strip()} ×{quantity}" if quantity is not None else product.strip())
    if note and note.strip():
        parts.append(note.strip())
    return " · ".join(parts)
