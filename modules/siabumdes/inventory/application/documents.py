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
    if model is Purchase:
        stmt = stmt.where(Purchase.payment_method != "modal_awal")
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
    rows = await trade_rows(session, kind, unit_id, partner_id, start, end, lock=lock)
    # Nota multi-baris: hitung baris lain supaya nama menyebut "+N item".
    extra = dict(
        (
            await session.execute(
                select(StockCard.nota_id, func.count()).where(
                    StockCard.nota_id.in_([c.id for _, c, _ in rows]), StockCard.id != StockCard.nota_id
                ).group_by(StockCard.nota_id)
            )
        ).all()
    ) if rows else {}
    for row, card, product in rows:
        total, paid, outstanding = balances(row)
        result.append(
            {
                "id": row.id,
                "partner_id": getattr(row, KINDS[kind][2]),
                "date": card.movement_date.isoformat(),
                "name": product.name + (f" +{extra[card.id]} item" if extra.get(card.id) else ""),
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


async def source_lines(session, kind, source_ids):
    """Rincian per baris nota untuk transaksi sumber: (tanggal, produk, qty, harga satuan, nilai).

    Penjualan: harga jual per baris (stock_cards.sell_price; data lama dibagi
    proporsional dari Sale.total_amount). Pembelian: harga beli (unit_cost).
    Nilai baris selalu dijumlahkan tepat ke total transaksi.
    """
    model = KINDS[kind][0]
    heads = dict((await session.execute(
        select(model.id, model.stock_card_id).where(model.id.in_(source_ids))
    )).all()) if source_ids else {}
    if not heads:
        return {}
    pairs = (await session.execute(
        select(StockCard, Product).join(Product, StockCard.product_id == Product.id)
        .where((StockCard.nota_id.in_(heads.values())) | (StockCard.id.in_(heads.values())))
        .order_by(StockCard.created_at, StockCard.id)
    )).all()
    by_head: dict = {}
    for card, product in pairs:
        by_head.setdefault(card.nota_id or card.id, []).append((card, product))
    values = {}
    if model is Sale:
        from modules.siabumdes.inventory.application.services import InventoryService

        values = await InventoryService(session)._sale_line_values([c for c, _ in pairs])
    totals = dict((await session.execute(select(model.id, model.total_amount).where(model.id.in_(heads)))).all())
    out = {}
    for sid, head in heads.items():
        lines = by_head.get(head, [])
        result, acc = [], Decimal(0)
        for i, (card, product) in enumerate(lines):
            value = Decimal(values.get(card.id, card.total_value))
            if i == len(lines) - 1:
                value = Decimal(totals[sid]) - acc
            acc += value
            price = (value / card.quantity).quantize(CENT) if card.quantity else Decimal(0)
            result.append({"date": card.movement_date.isoformat(), "name": product.name, "sku": product.sku,
                           "quantity": card.quantity, "unit": product.unit_of_measure,
                           "price": str(price), "value": value.quantize(CENT)})
        out[sid] = result
    return out


async def build_snapshot(session, body, unit, branding, lock_sources=False, allow_issued=False):
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
        details = await source_lines(session, body.kind, body.source_ids)
        for sid in body.source_ids:
            row = available.get(sid)
            if not row:
                raise HTTPException(422, "Transaksi sumber tidak sesuai mitra, unit, atau periode")
            if row["issued"] and not allow_issued:
                raise HTTPException(409, "Transaksi sudah dimasukkan ke dokumen yang diterbitkan")
            extra = extras.get(sid)
            packing, processing = (extra.packing, extra.processing) if extra else (Decimal(0), Decimal(0))
            total = Decimal(row["total"])
            if packing + processing > total:
                raise HTTPException(422, "Rincian biaya tidak boleh melebihi nilai transaksi yang sudah tercatat")
            lines = details.get(sid) or []
            if len(lines) <= 1 and not (packing or processing):
                lines = lines or [{"date": row["date"], "name": row["name"], "sku": row["sku"], "quantity": row["quantity"],
                                   "unit": row["unit"], "price": None, "value": total}]
            if len(lines) == 1 or packing or processing:
                # Satu baris (atau ada biaya tambahan): satu item per transaksi.
                first = lines[0] if len(lines) == 1 else None
                goods = total - packing - processing
                qty = row["quantity"] if not first else first["quantity"]
                items.append({
                    "source_id": sid, "date": row["date"], "name": first["name"] if first else row["name"],
                    "sku": row["sku"], "size": extra.size if extra else "", "order_code": row["invoice_number"],
                    "quantity": qty, "unit": row["unit"],
                    "price": str((goods / qty).quantize(CENT)) if qty else "0",
                    "goods": str(goods), "packing": str(packing), "processing": str(processing), "total": str(total),
                })
                continue
            for i, line in enumerate(lines):
                items.append({
                    # Hanya baris pertama membawa source_id (penanda transaksi sumber).
                    "source_id": sid if i == 0 else None,
                    "date": line["date"], "name": line["name"], "sku": line["sku"],
                    "size": extra.size if extra and i == 0 else "", "order_code": row["invoice_number"],
                    "quantity": line["quantity"], "unit": line["unit"], "price": line["price"],
                    "goods": str(line["value"]), "packing": "0", "processing": "0", "total": str(line["value"]),
                })
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
    issued_date = body.issued_date
    if body.kind in ("invoice", "po"):
        # Format mingguan (sama dengan PDF Perduitan): nomor, tanggal Sabtu, label minggu.
        from datetime import date as _date

        from modules.siabumdes.inventory.application.weekly_number import weekly_meta

        meta = await weekly_meta(session, body.kind, unit.id, body.partner_id, body.period_end)
        base, tail = meta["number"].split("/", 1)
        number, n = meta["number"], 1
        while await session.scalar(select(func.count()).select_from(CommercialDocument).where(CommercialDocument.number == number)):
            n += 1
            number = f"{base}-{n}/{tail}"  # dokumen kedua untuk mitra yang sama di minggu itu
        snapshot.update(meta, number=number)
        issued_date = _date.fromisoformat(meta["issued_date"])
    snapshot["number"] = number
    row = CommercialDocument(
        id=doc_id,
        number=number,
        kind=body.kind,
        unit_usaha_id=unit.id,
        partner_id=body.partner_id,
        issued_date=issued_date,
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
