"""Scoped commercial documents and complete partner statements."""

from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from modules.siabumdes.inventory.infrastructure.models import (
    CommercialDocument,
    Customer,
    DocumentSource,
    Product,
    Purchase,
    Sale,
    StockCard,
    Vendor,
)

CENT = Decimal("0.01")
KINDS = {"invoice": (Sale, Customer, "customer_id"), "purchase_statement": (Purchase, Vendor, "vendor_id")}


def balances(row):
    total = Decimal(row.total_amount)
    paid = total if row.payment_method == "cash" else sum((p.amount for p in row.payments), Decimal(0))
    return total, paid, max(Decimal(0), total - paid)


async def trade_rows(session, kind, unit_id, partner_id=None, start=None, end=None, lock=False):
    model, _, partner_field = KINDS[kind]
    stmt = (
        select(model, StockCard, Product)
        .join(StockCard, model.stock_card_id == StockCard.id)
        .join(Product, StockCard.product_id == Product.id)
        .where(Product.unit_usaha_id == unit_id, StockCard.finance_status == "posted")
        .options(selectinload(model.payments))
        .order_by(StockCard.movement_date, model.id)
    )
    if lock:
        stmt = stmt.with_for_update(of=StockCard)
    if partner_id:
        stmt = stmt.where(getattr(model, partner_field) == partner_id)
    if start:
        stmt = stmt.where(StockCard.movement_date >= start)
    if end:
        stmt = stmt.where(StockCard.movement_date <= end)
    return (await session.execute(stmt)).all()


async def sources(session, kind, unit_id, partner_id=None, start=None, end=None, lock=False):
    used = set(
        (
            await session.scalars(
                select(DocumentSource.source_id)
                .join(CommercialDocument, DocumentSource.document_id == CommercialDocument.id)
                .where(CommercialDocument.unit_usaha_id == unit_id, DocumentSource.kind == kind)
            )
        ).all()
    )
    result = []
    for row, card, product in await trade_rows(session, kind, unit_id, partner_id, start, end, lock=lock):
        total, paid, outstanding = balances(row)
        result.append(
            {
                "id": row.id,
                "partner_id": getattr(row, KINDS[kind][2]),
                "date": card.movement_date.isoformat(),
                "name": product.name,
                "sku": product.sku,
                "quantity": card.quantity,
                "unit": product.unit_of_measure,
                "invoice_number": row.invoice_number,
                "due_date": row.due_date.isoformat() if row.due_date else None,
                "total": str(total),
                "paid": str(paid),
                "outstanding": str(outstanding),
                "issued": row.id in used,
                "payments": [
                    {"date": p.paid_date.isoformat(), "amount": str(p.amount), "reference": p.reference}
                    for p in sorted(row.payments, key=lambda p: (p.paid_date, p.id))
                ],
            }
        )
    return result


async def build_snapshot(session, body, unit, branding, lock_sources=False):
    partner_model = Customer if body.kind == "invoice" else Vendor
    partner = await session.get(partner_model, body.partner_id)
    if not partner or partner.unit_usaha_id != unit.id:
        raise HTTPException(404, "Mitra tidak ditemukan pada unit ini")
    items = []
    if body.kind == "po":
        for line in body.lines:
            product = await session.get(Product, line.product_id)
            if not product or product.unit_usaha_id != unit.id:
                raise HTTPException(404, "Produk tidak ditemukan pada unit ini")
            total = (line.price * line.quantity).quantize(CENT)
            items.append(
                {
                    "source_id": None,
                    "date": line.date.isoformat(),
                    "name": product.name,
                    "sku": product.sku,
                    "size": line.size,
                    "order_code": line.order_code,
                    "quantity": line.quantity,
                    "unit": product.unit_of_measure,
                    "price": str(line.price),
                    "packing": "0",
                    "processing": "0",
                    "total": str(total),
                }
            )
    else:
        available = {
            r["id"]: r
            for r in await sources(
                session, body.kind, unit.id, partner.id, body.period_start, body.period_end, lock=lock_sources
            )
        }
        if not body.source_ids or len(set(body.source_ids)) != len(body.source_ids):
            raise HTTPException(422, "Pilih transaksi sumber yang berbeda")
        extras = {line.source_id: line for line in body.allocations}
        if set(extras) - set(body.source_ids) or len(extras) != len(body.allocations):
            raise HTTPException(422, "Rincian biaya harus sesuai transaksi yang dipilih")
        for sid in body.source_ids:
            row = available.get(sid)
            if not row:
                raise HTTPException(422, "Transaksi sumber tidak sesuai mitra, unit, atau periode")
            if row["issued"]:
                raise HTTPException(409, "Transaksi sudah dimasukkan ke dokumen yang diterbitkan")
            extra = extras.get(sid)
            packing, processing = (extra.packing, extra.processing) if extra else (Decimal(0), Decimal(0))
            total = Decimal(row["total"])
            if packing + processing > total:
                raise HTTPException(422, "Rincian biaya tidak boleh melebihi nilai transaksi yang sudah tercatat")
            items.append(
                {
                    "source_id": sid,
                    "date": row["date"],
                    "name": row["name"],
                    "sku": row["sku"],
                    "size": extra.size if extra else "",
                    "order_code": row["invoice_number"],
                    "quantity": row["quantity"],
                    "unit": row["unit"],
                    "price": str(((total - packing - processing) / row["quantity"]).quantize(CENT)),
                    "goods": str(total - packing - processing),
                    "packing": str(packing),
                    "processing": str(processing),
                    "total": str(total),
                }
            )
    total = sum((Decimal(i["total"]) for i in items), Decimal(0))
    if not items or total <= 0:
        raise HTTPException(422, "Dokumen harus memiliki rincian dengan total lebih dari nol")
    return {
        "kind": body.kind,
        "issued_date": body.issued_date.isoformat(),
        "due_date": body.due_date.isoformat() if body.due_date else None,
        "period_start": body.period_start.isoformat(),
        "period_end": body.period_end.isoformat(),
        "org_name": branding.org_name,
        "address": branding.address_block,
        "logo_url": branding.logo_url,
        "unit_name": unit.name,
        "partner_name": partner.name,
        "partner_address": partner.address or "",
        "partner_contact": partner.contact or "",
        "payment_info": body.payment_info,
        "items": items,
        "total": str(total),
        "number": "PRATINJAU",
    }


async def issue_document(session, body, unit, branding, actor):
    # Unit lock serializes issuance and avoids races over the same source rows.
    snapshot = await build_snapshot(session, body, unit, branding, lock_sources=True)
    doc_id = str(uuid4())
    prefix = {"invoice": "INV", "po": "PO", "purchase_statement": "REKAP"}[body.kind]
    count = await session.scalar(
        select(func.count())
        .select_from(CommercialDocument)
        .where(
            CommercialDocument.unit_usaha_id == unit.id,
            CommercialDocument.kind == body.kind,
            func.extract("year", CommercialDocument.issued_date) == body.issued_date.year,
        )
    )
    number = f"{prefix}/{unit.code}/{body.issued_date:%Y%m}/{count + 1:04d}"
    snapshot["number"] = number
    row = CommercialDocument(
        id=doc_id,
        number=number,
        kind=body.kind,
        unit_usaha_id=unit.id,
        partner_id=body.partner_id,
        issued_date=body.issued_date,
        snapshot=snapshot,
        created_by=actor.id,
    )
    session.add(row)
    await session.flush()
    for item in snapshot["items"]:
        if item["source_id"]:
            session.add(DocumentSource(document_id=doc_id, kind=body.kind, source_id=item["source_id"]))
    await session.flush()
    return row
