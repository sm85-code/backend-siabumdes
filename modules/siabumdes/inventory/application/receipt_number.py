"""Nomor nota otomatis Stock In / Stock Out.

Format: <nomor dokumen mingguan>-<urutan nota dalam dokumen itu>, mis.
  Stock In  (pemasok)   : PO-BUD-M1-001/X/2026-2
  Stock Out (pelanggan) : INV-ANI-M1-001/X/2026-1
  Tanpa mitra           : MA-M1/X/2026-1 (Belanja Modal Awal tanpa pemasok),
                          INT-M1/X/2026-1 (pemakaian internal)

Nomor diberikan di transaksi yang sama dengan penyimpanan nota, memakai
penghitung yang dikunci baris (INSERT .. ON CONFLICT DO UPDATE .. RETURNING),
disimpan di kartu kepala nota dan tidak pernah dihitung ulang. Penghitung
hanya naik: nota yang dibatalkan tidak membuat nomornya dipakai lagi.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from modules.siabumdes.inventory.application.weekly_number import (
    ROMAWI, doc_number, partner_code, week_of, weekly_meta,
)
from modules.siabumdes.inventory.infrastructure.models import (
    Customer, DocNumber, Product, Purchase, ReceiptCounter, Sale, StockCard, Vendor,
)

logger = logging.getLogger("sm85.receipt_number")

# jenis -> (prefix, model mitra)
KINDS = {"po": ("PO", Vendor), "invoice": ("INV", Customer), "ma": ("MA", None), "int": ("INT", None)}


def partnerless_base(prefix: str, d: date) -> str:
    monday, n = week_of(d)
    return f"{prefix}-M{n}/{ROMAWI[monday.month - 1]}/{monday.year}"


async def _bump_counter(session, unit_id: str, kind: str, week_start: date, partner_key: str) -> int:
    table = ReceiptCounter.__table__
    stmt = (
        pg_insert(table)
        .values(unit_usaha_id=unit_id, kind=kind, week_start=week_start, partner_key=partner_key, last_seq=1)
        .on_conflict_do_update(
            index_elements=["unit_usaha_id", "kind", "week_start", "partner_key"],
            set_={"last_seq": table.c.last_seq + 1},
        )
        .returning(table.c.last_seq)
    )
    return (await session.execute(stmt)).scalar_one()


async def assign_receipt_number(session, head: StockCard, *, kind: str, unit_id: str,
                                partner_id: Optional[str]) -> str:
    """Beri nomor ke kartu kepala nota (sekali saja). Harus dipanggil di transaksi penyimpanan nota."""
    if head.doc_number:
        return head.doc_number
    prefix, _ = KINDS[kind]
    monday, _ = week_of(head.movement_date)
    if partner_id and kind in ("po", "invoice"):
        base = (await weekly_meta(session, kind, unit_id, partner_id, head.movement_date))["number"]
    else:
        base, partner_id = partnerless_base(prefix, head.movement_date), None
    seq = await _bump_counter(session, unit_id, kind, monday, partner_id or "")
    head.doc_number = f"{base}-{seq}"
    head.doc_unit_id = unit_id
    await session.flush()
    return head.doc_number


async def weekly_number_preview(session, kind: str, unit_id: str, partner, d: date) -> str:
    """Nomor dokumen mingguan TANPA memesan urutan: pakai urutan yang sudah ada, jika belum ada
    perkiraan (urutan terbesar minggu itu + 1)."""
    prefix = "INV" if kind == "invoice" else "PO"
    monday, _ = week_of(d)
    key = (DocNumber.unit_usaha_id == unit_id, DocNumber.kind == kind, DocNumber.week_start == monday)
    seq = await session.scalar(select(DocNumber.seq).where(*key, DocNumber.partner_id == partner.id))
    if not seq:
        seq = (await session.scalar(select(func.max(DocNumber.seq)).where(*key)) or 0) + 1
    code = (partner.code or "").strip() or partner_code(partner.name)
    return doc_number(prefix, partner.name, d, seq, code)


async def preview_receipt_number(session, *, kind: str, unit_id: str, partner_id: Optional[str], d: date) -> str:
    """Perkiraan nomor nota berikutnya (tidak memesan apa pun; nomor final ditentukan saat simpan)."""
    prefix, model = KINDS[kind]
    monday, _ = week_of(d)
    partner = await session.get(model, partner_id) if (model is not None and partner_id) else None
    if model is not None and partner_id and (not partner or partner.unit_usaha_id != unit_id):
        raise ValueError("mitra tidak ditemukan")
    base = await weekly_number_preview(session, kind, unit_id, partner, d) if partner else partnerless_base(prefix, d)
    last = await session.scalar(select(ReceiptCounter.last_seq).where(
        ReceiptCounter.unit_usaha_id == unit_id, ReceiptCounter.kind == kind,
        ReceiptCounter.week_start == monday, ReceiptCounter.partner_key == (partner.id if partner else ""),
    ))
    return f"{base}-{(last or 0) + 1}"


async def backfill_receipt_numbers(session_factory) -> int:
    """Beri nomor ke nota lama yang belum bernomor: per unit, urut tanggal lalu waktu dibuat.

    Nomor mingguan yang sudah ada/terbit dipakai ulang (weekly_meta), jadi konsisten dengan
    dokumen INV/PO yang sudah diterbitkan. Idempoten; dikunci advisory lock agar aman multi-instance.
    """
    count = 0
    async with session_factory() as session:
        async with session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('receipt_doc_number_backfill'))"))
            rows = (await session.execute(
                select(StockCard, Product.unit_usaha_id, Purchase.id, Purchase.vendor_id, Sale.customer_id)
                .join(Product, Product.id == StockCard.product_id)
                .outerjoin(Purchase, Purchase.stock_card_id == StockCard.id)
                .outerjoin(Sale, Sale.stock_card_id == StockCard.id)
                .where(
                    StockCard.doc_number.is_(None),
                    or_(StockCard.nota_id.is_(None), StockCard.nota_id == StockCard.id),
                    StockCard.finance_status != "cancelled",
                )
                .order_by(Product.unit_usaha_id, StockCard.movement_date, StockCard.created_at, StockCard.id)
            )).all()
            for card, unit_id, purchase_id, vendor_id, customer_id in rows:
                if card.direction == "in" and purchase_id:
                    kind, partner = ("po", vendor_id) if vendor_id else ("ma", None)
                elif card.direction == "out" and card.movement_kind == "internal_use":
                    kind, partner = "int", None
                elif card.direction == "out" and customer_id:
                    kind, partner = "invoice", customer_id
                else:
                    continue  # data lama tanpa transaksi pembelian/penjualan: tidak diberi nomor
                await assign_receipt_number(session, card, kind=kind, unit_id=unit_id, partner_id=partner)
                count += 1
    if count:
        logger.info("backfill nomor nota: %s nota diberi nomor", count)
    return count
