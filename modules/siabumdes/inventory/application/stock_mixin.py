"""UU05 inventory stock movements."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from modules.siabumdes.inventory.application.descriptions import (
    belanja_modal_awal_nota_description,
    inventory_description,
    nota_products,
)
from modules.siabumdes.inventory.application.coa import validate_coa_codes
from modules.siabumdes.inventory.application.receipt_number import assign_receipt_number
from modules.siabumdes.inventory.infrastructure.models import (
    CommercialDocument,
    DocumentSource,
    Product,
    Purchase,
    Sale,
    StockCard,
    Vendor,
    Customer,
)

_PURCHASE_METHODS = {"cash", "credit", "modal_awal"}
# Belanja Modal Awal: modal sudah dicatat (Debit Kas / Kredit Modal), jadi
# pembelian dari modal itu mengkredit Kas, bukan Modal lagi.
MODAL_AWAL = "modal_awal"
_MODAL_AWAL_ACCOUNTS = ("1.1.05.15", "1.1.01.15")
_SALE_METHODS = {"cash", "piutang"}


class HppMismatchError(ValueError):
    """Stock In price differs from the HPP of a SKU that still has stock."""


def _rupiah(value: Decimal) -> str:
    q = Decimal(value).quantize(Decimal("0.01"))
    whole = f"{int(q):,}".replace(",", ".")
    cents = int((q - int(q)) * 100)
    return f"Rp {whole}" + (f",{cents:02d}" if cents else "")


def stock_in_price_error(product: Product, unit_cost: Decimal) -> Optional[str]:
    """Satu SKU = satu harga beli. Selama SKU masih punya stok, harga beli
    Stock In wajib sama dengan HPP-nya; harga berbeda harus memakai SKU baru.
    Stok 0 (belum pernah dipakai atau sudah habis) boleh menetapkan HPP baru.
    """
    cost = Decimal(product.cost_price or 0).quantize(Decimal("0.01"))
    if product.qty_on_hand > 0 and cost > 0 and Decimal(unit_cost).quantize(Decimal("0.01")) != cost:
        return f"Harga beli berbeda dengan HPP SKU ini ({_rupiah(cost)}). Buat SKU baru untuk harga baru."
    return None


def _line_error(index: int, product: Optional[Product], exc: Exception) -> str:
    """Pesan error nota multi-baris, contoh: 'Baris 2 [KRS.MM-OYK]: Stok tidak cukup (tersedia 3, diminta 5)'."""
    sku = f" [{product.sku}]" if product is not None and getattr(product, "sku", None) else ""
    return f"Baris {index}{sku}: {exc}"


class InventoryStockMixin:

    async def _get_product_for_update(self, product_id: str) -> Product:
        """Load Product row locked for the remainder of this transaction.

        Prevents check-then-act races on qty_on_hand under concurrent
        stock_in / stock_out / stock_out_internal / cancel_movement.
        """
        product = await self.session.scalar(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        if not product:
            raise ValueError("produk tidak ditemukan")
        return product

    async def _decrement_qty_atomic(self, product_id: str, quantity: int) -> Product:
        """Atomically decrement qty_on_hand when sufficient stock remains.

        ``UPDATE ... WHERE qty_on_hand >= :q`` is the belt alongside
        SELECT FOR UPDATE: even if a caller skipped the lock, concurrent
        decrements cannot drive qty negative.
        """
        stmt = (
            update(Product)
            .where(Product.id == product_id, Product.qty_on_hand >= quantity)
            .values(qty_on_hand=Product.qty_on_hand - quantity)
            .returning(Product)
        )
        product = (await self.session.scalars(stmt)).first()
        if product is not None:
            return product
        exists = await self.session.get(Product, product_id)
        if not exists:
            raise ValueError("produk tidak ditemukan")
        raise ValueError(
            f"Stok tidak cukup (tersedia {exists.qty_on_hand}, diminta {quantity})"
        )

    async def stock_in(
        self,
        *,
        product_id: str,
        quantity: int,
        unit_cost: Decimal,
        **header: Any,
    ) -> StockCard:
        cards = await self.stock_in_nota(
            lines=[{"product_id": product_id, "quantity": quantity, "unit_cost": unit_cost}], **header
        )
        return cards[0]

    async def stock_in_nota(
        self,
        *,
        lines: list[dict[str, Any]],
        movement_date: date,
        unit_usaha_id: str,
        debit_account_code: str,
        credit_account_code: str,
        vendor_id: Optional[str],
        invoice_number: str = "",
        payment_method: str = "cash",
        due_date: Optional[date] = None,
        created_by: str = "system-inventory",
    ) -> list[StockCard]:
        """Satu nota pembelian: banyak baris barang, satu jurnal, satu Purchase.

        Semua baris berada di satu transaksi DB; satu baris gagal -> semua batal
        (pemanggil rollback). Kartu stok pertama menjadi kepala nota
        (nota_id = id kartu pertama; referensi jurnal stock-in:<id kepala>).
        """
        if not lines:
            raise ValueError("minimal satu barang")
        if payment_method not in _PURCHASE_METHODS:
            raise ValueError("metode pembayaran pembelian tidak valid")
        if payment_method == "credit" and not due_date:
            raise ValueError("tanggal jatuh tempo wajib diisi untuk pembelian kredit")
        modal_awal = payment_method == MODAL_AWAL
        if modal_awal:
            debit_account_code, credit_account_code = _MODAL_AWAL_ACCOUNTS
            due_date = None
        vendor = await self.session.get(Vendor, vendor_id) if vendor_id else None
        if not vendor and not (modal_awal and not vendor_id):
            raise ValueError("pemasok tidak ditemukan")
        multi = len(lines) > 1
        cards: list[tuple[StockCard, Product]] = []
        total = Decimal("0.00")
        for i, line in enumerate(lines, start=1):
            quantity = int(line["quantity"])
            unit_cost = Decimal(line["unit_cost"])
            product = None
            try:
                if quantity <= 0:
                    raise ValueError("jumlah harus > 0")
                product = await self._get_product_for_update(line["product_id"])
                price_error = stock_in_price_error(product, unit_cost)
                if price_error:
                    raise HppMismatchError(price_error)
            except ValueError as exc:
                if not multi:
                    raise
                raise type(exc)(_line_error(i, product, exc)) from exc
            line_total = (unit_cost * quantity).quantize(Decimal("0.01"))
            total += line_total
            card = StockCard(
                product_id=product.id,
                movement_date=movement_date,
                direction="in",
                quantity=quantity,
                unit_cost=unit_cost,
                total_value=line_total,
                reference="",
                finance_status="pending",
            )
            if product.qty_on_hand <= 0 or not product.cost_price:
                # Stok kosong: tidak ada stok lama yang salah dinilai.
                product.cost_price = unit_cost
            product.qty_on_hand += quantity
            self.session.add(card)
            await self.session.flush()
            cards.append((card, product))
        head = cards[0][0]
        for card, _ in cards:
            card.nota_id = head.id
            card.reference = f"stock-in:{head.id}"
        items = [(p.sku, p.name, c.quantity, p.unit_of_measure) for c, p in cards]
        await validate_coa_codes(self.session, debit_account_code, credit_account_code)
        await self.finance.record_inventory_journal(
            movement_date=movement_date,
            unit_usaha_id=unit_usaha_id,
            amount=total,
            debit_account_code=debit_account_code,
            credit_account_code=credit_account_code,
            description=(
                belanja_modal_awal_nota_description(items) if modal_awal else
                inventory_description("Pembelian Stok", partner=vendor.name, invoice=invoice_number,
                                      product=nota_products(items), quantity=None if multi else items[0][2])
            ),
            reference=head.reference,
            created_by=created_by,
            transaction_type="inventory_purchase",
        )
        for card, _ in cards:
            card.finance_status = "posted"
        purchase = Purchase(
            stock_card_id=head.id,
            vendor_id=vendor_id,
            invoice_number=invoice_number or "",
            payment_method=payment_method,
            total_amount=total,
            due_date=due_date if payment_method == "credit" else None,
            status="unpaid" if payment_method == "credit" else "paid",
        )
        self.session.add(purchase)
        await self.session.flush()
        await assign_receipt_number(self.session, head, kind="po" if vendor_id else "ma",
                                    unit_id=unit_usaha_id, partner_id=vendor_id)
        return [c for c, _ in cards]

    async def stock_out(
        self,
        *,
        product_id: str,
        quantity: int,
        sell_price: Decimal,
        **header: Any,
    ) -> StockCard:
        cards = await self.stock_out_nota(
            lines=[{"product_id": product_id, "quantity": quantity, "sell_price": sell_price}], **header
        )
        return cards[0]

    async def stock_out_nota(
        self,
        *,
        lines: list[dict[str, Any]],
        movement_date: date,
        unit_usaha_id: str,
        debit_account_code: str,
        credit_account_code: str,
        customer_id: str,
        revenue_debit_account_code: str,
        revenue_credit_account_code: str,
        invoice_number: str = "",
        payment_method: str = "cash",
        due_date: Optional[date] = None,
        created_by: str = "system-inventory",
    ) -> list[StockCard]:
        """Satu nota penjualan: banyak baris, satu jurnal HPP + satu jurnal pendapatan, satu Sale."""
        if not lines:
            raise ValueError("minimal satu barang")
        if payment_method not in _SALE_METHODS:
            raise ValueError("metode pembayaran penjualan tidak valid")
        if payment_method == "piutang" and not due_date:
            raise ValueError("tanggal jatuh tempo wajib diisi untuk penjualan piutang")
        customer = await self.session.get(Customer, customer_id)
        if not customer:
            raise ValueError("pelanggan tidak ditemukan")
        multi = len(lines) > 1
        cards: list[tuple[StockCard, Product]] = []
        total = Decimal("0.00")
        revenue_total = Decimal("0.00")
        for i, line in enumerate(lines, start=1):
            quantity = int(line["quantity"])
            sell_price = Decimal(line["sell_price"])
            product = None
            try:
                if quantity <= 0:
                    raise ValueError("jumlah harus > 0")
                if sell_price < 0:
                    raise ValueError("harga jual tidak boleh negatif")
                # Lock first so cost_price / existence are stable, then atomic
                # decrement so two concurrent outs cannot both pass a stale check.
                product = await self._get_product_for_update(line["product_id"])
                unit_cost = product.cost_price
                product = await self._decrement_qty_atomic(product.id, quantity)
            except ValueError as exc:
                if not multi:
                    raise
                raise ValueError(_line_error(i, product, exc)) from exc
            line_total = (unit_cost * quantity).quantize(Decimal("0.01"))
            total += line_total
            revenue_total += (sell_price * quantity).quantize(Decimal("0.01"))
            card = StockCard(
                product_id=product.id,
                movement_date=movement_date,
                direction="out",
                quantity=quantity,
                unit_cost=unit_cost,
                total_value=line_total,
                sell_price=sell_price,
                reference="",
                finance_status="pending",
            )
            self.session.add(card)
            await self.session.flush()
            cards.append((card, product))
        head = cards[0][0]
        for card, _ in cards:
            card.nota_id = head.id
            card.reference = f"stock-out:{head.id}"
        items = [(p.sku, p.name, c.quantity, p.unit_of_measure) for c, p in cards]
        products = nota_products(items)
        qty = None if multi else items[0][2]
        await validate_coa_codes(self.session, debit_account_code, credit_account_code)
        await self.finance.record_inventory_journal(
            movement_date=movement_date,
            unit_usaha_id=unit_usaha_id,
            amount=total,
            debit_account_code=debit_account_code,
            credit_account_code=credit_account_code,
            description=inventory_description("HPP Penjualan", invoice=invoice_number, product=products, quantity=qty),
            reference=head.reference,
            created_by=created_by,
            transaction_type="inventory_cogs",
        )
        await validate_coa_codes(self.session, revenue_debit_account_code, revenue_credit_account_code)
        if revenue_total > 0:
            await self.finance.record_inventory_journal(
                movement_date=movement_date,
                unit_usaha_id=unit_usaha_id,
                amount=revenue_total,
                debit_account_code=revenue_debit_account_code,
                credit_account_code=revenue_credit_account_code,
                description=inventory_description("Penjualan", partner=customer.name, invoice=invoice_number, product=products, quantity=qty),
                reference=f"stock-out-rev:{head.id}",
                created_by=created_by,
                transaction_type="inventory_sale_revenue",
            )
        for card, _ in cards:
            card.finance_status = "posted"
        sale = Sale(
            stock_card_id=head.id,
            customer_id=customer_id,
            invoice_number=invoice_number or "",
            # Nota banyak baris: harga per baris ada di total; sell_price = baris pertama.
            sell_price=Decimal(lines[0]["sell_price"]),
            payment_method=payment_method,
            total_amount=revenue_total,
            due_date=due_date if payment_method == "piutang" else None,
            status="paid" if payment_method == "cash" else "unpaid",
        )
        self.session.add(sale)
        await self.session.flush()
        await assign_receipt_number(self.session, head, kind="invoice", unit_id=unit_usaha_id, partner_id=customer_id)
        return [c for c, _ in cards]

    async def stock_out_internal(
        self,
        *,
        product_id: str,
        quantity: int,
        movement_date: date,
        unit_usaha_id: str,
        debit_account_code: str,
        credit_account_code: str,
        note: str = "",
        created_by: str = "system-inventory",
    ) -> StockCard:
        """Stock out untuk pemakaian/transfer internal -- bukan penjualan.

        Beda dari stock_out(): tidak ada customer_id/sell_price, tidak ada
        record Sale (jadi tidak muncul di tab Piutang), dan cuma posting
        SATU jurnal (beban pemakaian, bukan HPP+Pendapatan) karena tidak
        ada transaksi jual-beli dengan pihak luar yang terjadi.

        Fungsi terpisah dari stock_out() (bukan parameter opsional di situ)
        supaya alur penjualan yang sudah teruji tidak ikut berubah.
        """
        if quantity <= 0:
            raise ValueError("jumlah harus > 0")
        product = await self._get_product_for_update(product_id)
        unit_cost = product.cost_price
        product = await self._decrement_qty_atomic(product_id, quantity)
        total = (unit_cost * quantity).quantize(Decimal("0.01"))
        card = StockCard(
            product_id=product_id,
            movement_date=movement_date,
            direction="out",
            movement_kind="internal_use",
            quantity=quantity,
            unit_cost=unit_cost,
            total_value=total,
            reference="",
            finance_status="pending",
        )
        self.session.add(card)
        await self.session.flush()
        card.reference = f"stock-out-internal:{card.id}"
        await validate_coa_codes(self.session, debit_account_code, credit_account_code)
        desc = inventory_description("Pemakaian Internal", product=product.name, quantity=quantity, note=note)
        await self.finance.record_inventory_journal(
            movement_date=movement_date,
            unit_usaha_id=unit_usaha_id,
            amount=total,
            debit_account_code=debit_account_code,
            credit_account_code=credit_account_code,
            description=desc,
            reference=card.reference,
            created_by=created_by,
            transaction_type="inventory_internal_use",
        )
        card.finance_status = "posted"
        await self.session.flush()
        await assign_receipt_number(self.session, card, kind="int", unit_id=unit_usaha_id, partner_id=None)
        return card

    async def nota_cards(self, card: StockCard) -> list[StockCard]:
        """Semua baris satu nota (kepala dulu). Data lama tanpa nota_id = 1 baris."""
        if not card.nota_id:
            return [card]
        rows = (await self.session.scalars(
            select(StockCard).where(StockCard.nota_id == card.nota_id)
            .order_by(StockCard.created_at).with_for_update()
        )).all()
        return sorted(rows, key=lambda c: c.id != card.nota_id) or [card]

    async def cancel_movement(self, stock_card_id: str) -> None:
        """Batalkan satu nota (semua barisnya): balik qty, hapus jurnal, purchase/sale, kartu stok."""
        card = await self.session.get(StockCard, stock_card_id, with_for_update=True)
        if not card:
            return
        cards = await self.nota_cards(card)
        head_id = card.nota_id or card.id
        documented = await self.session.scalar(
            select(CommercialDocument.number).join(DocumentSource, DocumentSource.document_id == CommercialDocument.id)
            .where(CommercialDocument.status == 'issued', DocumentSource.source_id.in_(
                select(Purchase.id).where(Purchase.stock_card_id == head_id).union(select(Sale.id).where(Sale.stock_card_id == head_id))
            )).limit(1)
        )
        if documented:
            raise ValueError(f'Batalkan dokumen {documented} terlebih dahulu sebelum membatalkan transaksi stok')
        if all(c.finance_status == "cancelled" for c in cards):
            # Leftover soft-cancelled row from older builds — just purge.
            for c in cards:
                await self.session.delete(c)
            await self.session.flush()
            return
        for c in cards:
            product = await self.session.scalar(
                select(Product).where(Product.id == c.product_id).with_for_update()
            )
            if not product:
                continue
            if c.direction == "in":
                if product.qty_on_hand < c.quantity:
                    raise ValueError(f"Stok {product.name} tidak cukup untuk membatalkan Stock In ini")
                product.qty_on_hand -= c.quantity
            else:
                product.qty_on_hand += c.quantity

        purchase = await self.session.scalar(
            select(Purchase)
            .options(selectinload(Purchase.payments))
            .where(Purchase.stock_card_id == head_id)
        )
        if purchase:
            for payment in list(purchase.payments):
                if payment.reference:
                    await self.finance.cancel_inventory_journal(payment.reference)
                await self.session.delete(payment)
            await self.session.delete(purchase)

        sale = await self.session.scalar(
            select(Sale)
            .options(selectinload(Sale.payments))
            .where(Sale.stock_card_id == head_id)
        )
        if sale:
            for payment in list(sale.payments):
                if payment.reference:
                    await self.finance.cancel_inventory_journal(payment.reference)
                await self.session.delete(payment)
            await self.finance.cancel_inventory_journal(f"stock-out-rev:{head_id}")
            await self.session.delete(sale)

        for ref in dict.fromkeys(c.reference for c in cards if c.reference):
            await self.finance.cancel_inventory_journal(ref)
        await self.session.flush()
        for c in sorted(cards, key=lambda c: c.id == head_id):
            await self.session.delete(c)
        await self.session.flush()

    async def list_movements(
        self,
        *,
        product_id: Optional[str] = None,
        direction: Optional[str] = None,
        limit: int = 100,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(StockCard)
            .options(selectinload(StockCard.product).selectinload(Product.category))
            .order_by(StockCard.movement_date.desc(), StockCard.created_at.desc())
            .limit(min(limit, 2000))
        )
        if date_from:
            stmt = stmt.where(StockCard.movement_date >= date_from)
        if date_to:
            stmt = stmt.where(StockCard.movement_date <= date_to)
        if product_id:
            stmt = stmt.where(StockCard.product_id == product_id)
        if direction in {"in", "out"}:
            stmt = stmt.where(StockCard.direction == direction)
        # Cancelled rows are purged on cancel; hide any legacy soft-cancels.
        stmt = stmt.where(StockCard.finance_status != "cancelled")
        rows = (await self.session.scalars(stmt)).all()
        heads = {c.nota_id or c.id for c in rows}
        docs = {r[0]: (r[1], r[2]) for r in (await self.session.execute(
            select(Purchase.stock_card_id, Purchase.invoice_number, Purchase.payment_method).where(Purchase.stock_card_id.in_(heads))
            .union_all(select(Sale.stock_card_id, Sale.invoice_number, Sale.payment_method).where(Sale.stock_card_id.in_(heads)))
        )).all()} if heads else {}
        sale_values = await self._sale_line_values(rows)
        head_numbers = dict((await self.session.execute(
            select(StockCard.id, StockCard.doc_number).where(StockCard.id.in_(heads))
        )).all()) if heads else {}
        return [
            {
                "id": c.id,
                "product_id": c.product_id,
                "sku": c.product.sku if c.product else None,
                "product_name": c.product.name if c.product else None,
                "movement_date": c.movement_date.isoformat(),
                "direction": c.direction,
                "movement_kind": c.movement_kind,
                "quantity": c.quantity,
                "unit_cost": str(c.unit_cost),
                "total_value": str(c.total_value),
                "reference": c.reference,
                "finance_status": c.finance_status,
                "nota_id": c.nota_id or c.id,
                "invoice_number": (docs.get(c.nota_id or c.id) or ("", ""))[0] or "",
                "doc_number": head_numbers.get(c.nota_id or c.id) or "",
                "payment_method": (docs.get(c.nota_id or c.id) or ("", ""))[1] or "",
                "sale_value": str(sale_values[c.id]) if c.id in sale_values else None,
            }
            for c in rows
        ]

    async def _sale_line_values(self, rows) -> dict[str, Decimal]:
        """Nilai jual per baris Stock Out penjualan.

        Pakai qty x sell_price baris bila tersimpan; data lama (sell_price NULL)
        membagi Sale.total_amount nota secara proporsional terhadap HPP baris.
        """
        heads = {c.nota_id or c.id for c in rows if c.direction == "out"}
        if not heads:
            return {}
        sales = dict((await self.session.execute(
            select(Sale.stock_card_id, Sale.total_amount).where(Sale.stock_card_id.in_(heads))
        )).all())
        if not sales:
            return {}
        lines = (await self.session.scalars(
            select(StockCard).where((StockCard.nota_id.in_(sales.keys())) | (StockCard.id.in_(sales.keys())))
        )).all()
        by_nota: dict[str, list[StockCard]] = {}
        for c in lines:
            by_nota.setdefault(c.nota_id or c.id, []).append(c)
        out: dict[str, Decimal] = {}
        for nota, cards in by_nota.items():
            known = {c.id: (c.sell_price * c.quantity).quantize(Decimal("0.01")) for c in cards if c.sell_price is not None}
            rest = [c for c in cards if c.sell_price is None]
            remaining = Decimal(sales[nota]) - sum(known.values(), Decimal("0.00"))
            base = sum((c.total_value for c in rest), Decimal("0.00"))
            for c in rest:
                share = (c.total_value / base) if base else Decimal(1) / len(rest)
                known[c.id] = (remaining * share).quantize(Decimal("0.01"))
            out.update(known)
        return out
