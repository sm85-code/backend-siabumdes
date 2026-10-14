"""Readable inventory labels and issued-document links, without rewriting journals."""

from sqlalchemy import select

from modules.siabumdes.infrastructure.models import Transaction
from modules.siabumdes.inventory.application.descriptions import (
    belanja_modal_awal_nota_description,
    inventory_description,
    nota_products,
)
from modules.siabumdes.inventory.infrastructure.models import (
    CommercialDocument,
    DocumentSource,
    Product,
    Purchase,
    PurchasePayment,
    Sale,
    SalePayment,
    StockCard,
    Vendor,
    Customer,
)


async def transaction_labels(session, unit_id, references):
    transactions = (
        await session.scalars(
            select(Transaction).where(Transaction.unit_usaha_id == unit_id, Transaction.reference.in_(references))
        )
    ).all()
    refs = {t.reference for t in transactions}
    ids = {r.split(":", 1)[1] for r in refs if r.startswith(("stock-in:", "stock-out:", "stock-out-rev:"))}
    rows = (
        await session.execute(
            select(StockCard, Product, Purchase, Sale, Vendor, Customer)
            .join(Product, StockCard.product_id == Product.id)
            .outerjoin(Purchase, Purchase.stock_card_id == StockCard.id)
            .outerjoin(Sale, Sale.stock_card_id == StockCard.id)
            .outerjoin(Vendor, Purchase.vendor_id == Vendor.id)
            .outerjoin(Customer, Sale.customer_id == Customer.id)
            .where(StockCard.id.in_(ids), Product.unit_usaha_id == unit_id)
        )
    ).all()
    # Nota multi-baris: kartu kepala (id di referensi) + baris lain (nota_id sama).
    lines = {}
    for line, line_product in (
        await session.execute(
            select(StockCard, Product).join(Product, StockCard.product_id == Product.id)
            .where(StockCard.nota_id.in_(ids), Product.unit_usaha_id == unit_id)
            .order_by(StockCard.created_at)
        )
    ).all():
        lines.setdefault(line.nota_id, []).append(line)
        lines[line.nota_id].sort(key=lambda c: c.id != line.nota_id)
        line.product = line_product
    source_ids = [p.id if p else s.id for _, _, p, s, _, _ in rows if p or s]
    links = {
        sid: (doc.id, doc.number)
        for sid, doc in (
            await session.execute(
                select(DocumentSource.source_id, CommercialDocument)
                .join(CommercialDocument, DocumentSource.document_id == CommercialDocument.id)
                .where(
                    DocumentSource.source_id.in_(source_ids),
                    CommercialDocument.unit_usaha_id == unit_id,
                    CommercialDocument.status == "issued",
                )
            )
        ).all()
    }
    result = {}
    for card, product, purchase, sale, vendor, customer in rows:
        source = purchase or sale
        link = links.get(source.id) if source else None
        candidates = [
            ("stock-in:", "Pembelian Stok", vendor),
            ("stock-out:", "HPP Penjualan", None),
            ("stock-out-rev:", "Penjualan", customer),
        ]
        for prefix, action, partner in candidates:
            ref = prefix + card.id
            if ref not in refs:
                continue
            modal_awal = prefix == "stock-in:" and purchase is not None and purchase.payment_method == "modal_awal"
            nota = lines.get(card.id) or [card]
            items = [
                (c.product.sku, c.product.name, c.quantity, c.product.unit_of_measure) if c is not card
                else (product.sku, product.name, c.quantity, product.unit_of_measure)
                for c in nota
            ]
            result[ref] = {
                "description": belanja_modal_awal_nota_description(items) if modal_awal else inventory_description(
                    action,
                    partner=partner.name if partner else None,
                    invoice=source.invoice_number if source else None,
                    product=nota_products(items),
                    quantity=card.quantity if len(items) == 1 else None,
                ),
                "document_id": link[0] if link else None,
                "document_number": link[1] if link else None,
            }
    for payment_model, trade_model, partner_model, trade_key, partner_key, prefix, action in (
        (PurchasePayment, Purchase, Vendor, "purchase_id", "vendor_id", "purchase-pay:", "Pembayaran Pemasok"),
        (SalePayment, Sale, Customer, "sale_id", "customer_id", "sale-pay:", "Pembayaran Pelanggan"),
    ):
        payment_ids = [r.split(":", 1)[1] for r in refs if r.startswith(prefix)]
        if not payment_ids:
            continue
        payments = (
            await session.execute(
                select(payment_model, trade_model, partner_model)
                .join(trade_model, getattr(payment_model, trade_key) == trade_model.id)
                .join(partner_model, getattr(trade_model, partner_key) == partner_model.id)
                .where(payment_model.id.in_(payment_ids), partner_model.unit_usaha_id == unit_id)
            )
        ).all()
        issued = {
            sid: (doc.id, doc.number)
            for sid, doc in (
                await session.execute(
                    select(DocumentSource.source_id, CommercialDocument)
                    .join(CommercialDocument, DocumentSource.document_id == CommercialDocument.id)
                    .where(
                        DocumentSource.source_id.in_([t.id for _, t, _ in payments]),
                        CommercialDocument.unit_usaha_id == unit_id,
                        CommercialDocument.status == "issued",
                    )
                )
            ).all()
        }
        for payment, trade, partner in payments:
            doc = issued.get(trade.id)
            result[prefix + payment.id] = {
                "description": inventory_description(
                    action, partner=partner.name, invoice=trade.invoice_number or (doc[1] if doc else None)
                ),
                "document_id": doc[0] if doc else None,
                "document_number": doc[1] if doc else None,
            }
    return result
