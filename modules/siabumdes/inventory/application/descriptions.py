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


def belanja_modal_awal_nota_description(items) -> str:
    """items: [(sku, name, qty, unit)]. 1 baris = format lama; banyak baris
    digabung '; ', contoh: Belanja Modal Awal - [A] Oyek - 10 kg; [B] Gula - 5 kg.
    Lebih dari 5 baris diringkas: ... (+N item lain)."""
    if len(items) == 1:
        sku, name, qty, unit = items[0]
        return belanja_modal_awal_description(sku=sku, name=name, quantity=qty, unit=unit)
    parts = [
        f"[{(sku or '').strip()}] {(name or '').strip()} - {format_qty(qty)} {(unit or '').strip() or 'pcs'}"
        for sku, name, qty, unit in items[:5]
    ]
    more = f" (+{len(items) - 5} item lain)" if len(items) > 5 else ""
    return "Belanja Modal Awal - " + "; ".join(parts) + more


def nota_products(items) -> str:
    """Nama produk untuk keterangan nota. 1 baris: nama saja (×qty ditambah
    inventory_description). Banyak baris: 'Oyek ×10, Gula ×5'."""
    if len(items) == 1:
        return items[0][1]
    text = ", ".join(f"{(name or '').strip()} ×{format_qty(qty)}" for _, name, qty, _ in items[:5])
    return text + (f" (+{len(items) - 5} item lain)" if len(items) > 5 else "")


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
