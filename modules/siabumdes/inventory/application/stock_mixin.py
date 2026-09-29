"""UU05 inventory stock movements."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from modules.siabumdes.inventory.application.coa import validate_coa_codes
from modules.siabumdes.inventory.infrastructure.models import (
    Product,
    Purchase,
    Sale,
    StockCard,
    Vendor,
    Customer,
)

_PURCHASE_METHODS = {"cash", "credit"}
_SALE_METHODS = {"cash", "piutang"}


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
            raise ValueError("product not found")
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
            raise ValueError("product not found")
        raise ValueError("insufficient stock")

    async def stock_in(
        self,
        *,
        product_id: str,
        quantity: int,
        unit_cost: Decimal,
        movement_date: date,
        unit_usaha_id: str,
        debit_account_code: str,
        credit_account_code: str,
        vendor_id: str,
        invoice_number: str = "",
        payment_method: str = "cash",
        due_date: Optional[date] = None,
        created_by: str = "system-inventory",
    ) -> StockCard:
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        if payment_method not in _PURCHASE_METHODS:
            raise ValueError("metode pembayaran pembelian tidak valid")
        if payment_method == "credit" and not due_date:
            raise ValueError("tanggal jatuh tempo wajib diisi untuk pembelian kredit")
        product = await self._get_product_for_update(product_id)
        vendor = await self.session.get(Vendor, vendor_id)
        if not vendor:
            raise ValueError("vendor tidak ditemukan")
        total = (unit_cost * quantity).quantize(Decimal("0.01"))
        card = StockCard(
            product_id=product_id,
            movement_date=movement_date,
            direction="in",
            quantity=quantity,
            unit_cost=unit_cost,
            total_value=total,
            reference="",
            finance_status="pending",
        )
        product.qty_on_hand += quantity
        product.cost_price = unit_cost
        self.session.add(card)
        await self.session.flush()
        card.reference = f"stock-in:{card.id}"
        await validate_coa_codes(self.session, debit_account_code, credit_account_code)
        await self.finance.record_inventory_journal(
            movement_date=movement_date,
            unit_usaha_id=unit_usaha_id,
            amount=total,
            debit_account_code=debit_account_code,
            credit_account_code=credit_account_code,
            description=f"Stok masuk {product.sku} x{quantity}",
            reference=card.reference,
            created_by=created_by,
            transaction_type="inventory_purchase",
        )
        card.finance_status = "posted"
        purchase = Purchase(
            stock_card_id=card.id,
            vendor_id=vendor_id,
            invoice_number=invoice_number or "",
            payment_method=payment_method,
            total_amount=total,
            due_date=due_date if payment_method == "credit" else None,
            status="paid" if payment_method == "cash" else "unpaid",
        )
        self.session.add(purchase)
        await self.session.flush()
        return card

    async def stock_out(
        self,
        *,
        product_id: str,
        quantity: int,
        movement_date: date,
        unit_usaha_id: str,
        debit_account_code: str,
        credit_account_code: str,
        customer_id: str,
        sell_price: Decimal,
        revenue_debit_account_code: str,
        revenue_credit_account_code: str,
        invoice_number: str = "",
        payment_method: str = "cash",
        due_date: Optional[date] = None,
        created_by: str = "system-inventory",
    ) -> StockCard:
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        if sell_price < 0:
            raise ValueError("harga jual tidak boleh negatif")
        if payment_method not in _SALE_METHODS:
            raise ValueError("metode pembayaran penjualan tidak valid")
        if payment_method == "piutang" and not due_date:
            raise ValueError("tanggal jatuh tempo wajib diisi untuk penjualan piutang")
        # Lock first so cost_price / existence are stable, then atomic
        # decrement so two concurrent outs cannot both pass a stale check.
        product = await self._get_product_for_update(product_id)
        customer = await self.session.get(Customer, customer_id)
        if not customer:
            raise ValueError("customer tidak ditemukan")
        unit_cost = product.cost_price
        product = await self._decrement_qty_atomic(product_id, quantity)
        total = (unit_cost * quantity).quantize(Decimal("0.01"))
        card = StockCard(
            product_id=product_id,
            movement_date=movement_date,
            direction="out",
            quantity=quantity,
            unit_cost=unit_cost,
            total_value=total,
            reference="",
            finance_status="pending",
        )
        self.session.add(card)
        await self.session.flush()
        card.reference = f"stock-out:{card.id}"
        await validate_coa_codes(self.session, debit_account_code, credit_account_code)
        await self.finance.record_inventory_journal(
            movement_date=movement_date,
            unit_usaha_id=unit_usaha_id,
            amount=total,
            debit_account_code=debit_account_code,
            credit_account_code=credit_account_code,
            description=f"Stok keluar / COGS {product.sku} x{quantity}",
            reference=card.reference,
            created_by=created_by,
            transaction_type="inventory_cogs",
        )

        revenue_total = (sell_price * quantity).quantize(Decimal("0.01"))
        await validate_coa_codes(self.session, revenue_debit_account_code, revenue_credit_account_code)
        revenue_reference = f"stock-out-rev:{card.id}"
        if revenue_total > 0:
            await self.finance.record_inventory_journal(
                movement_date=movement_date,
                unit_usaha_id=unit_usaha_id,
                amount=revenue_total,
                debit_account_code=revenue_debit_account_code,
                credit_account_code=revenue_credit_account_code,
                description=f"Penjualan {product.sku} x{quantity}",
                reference=revenue_reference,
                created_by=created_by,
                transaction_type="inventory_sale_revenue",
            )

        card.finance_status = "posted"
        sale = Sale(
            stock_card_id=card.id,
            customer_id=customer_id,
            invoice_number=invoice_number or "",
            sell_price=sell_price,
            payment_method=payment_method,
            total_amount=revenue_total,
            due_date=due_date if payment_method == "piutang" else None,
            status="paid" if payment_method == "cash" else "unpaid",
        )
        self.session.add(sale)
        await self.session.flush()
        return card

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
            raise ValueError("quantity must be > 0")
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
        desc = f"Pemakaian internal {product.sku} x{quantity}"
        if note.strip():
            desc = f"{desc} ({note.strip()})"
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
        return card

    async def cancel_movement(self, stock_card_id: str) -> None:
        """Reverse qty, remove finance journal(s), purchase/sale records, and the stock card.

        Soft-cancel left cancelled rows (and confusing \"jurnal dibatalkan\") in the UI;
        callers expect cancel to leave the ledger clean.
        """
        card = await self.session.get(StockCard, stock_card_id)
        if not card:
            return
        if card.finance_status == "cancelled":
            # Leftover soft-cancelled row from older builds — just purge.
            await self.session.delete(card)
            await self.session.flush()
            return
        product = await self.session.scalar(
            select(Product).where(Product.id == card.product_id).with_for_update()
        )
        if product:
            if card.direction == "in":
                product.qty_on_hand = max(0, product.qty_on_hand - card.quantity)
            else:
                product.qty_on_hand += card.quantity

        purchase = await self.session.scalar(
            select(Purchase)
            .options(selectinload(Purchase.payments))
            .where(Purchase.stock_card_id == card.id)
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
            .where(Sale.stock_card_id == card.id)
        )
        if sale:
            for payment in list(sale.payments):
                if payment.reference:
                    await self.finance.cancel_inventory_journal(payment.reference)
                await self.session.delete(payment)
            await self.finance.cancel_inventory_journal(f"stock-out-rev:{card.id}")
            await self.session.delete(sale)

        if card.reference:
            await self.finance.cancel_inventory_journal(card.reference)
        await self.session.flush()
        await self.session.delete(card)
        await self.session.flush()

    async def list_movements(
        self,
        *,
        product_id: Optional[str] = None,
        direction: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(StockCard)
            .options(selectinload(StockCard.product).selectinload(Product.category))
            .order_by(StockCard.movement_date.desc(), StockCard.created_at.desc())
            .limit(min(limit, 500))
        )
        if product_id:
            stmt = stmt.where(StockCard.product_id == product_id)
        if direction in {"in", "out"}:
            stmt = stmt.where(StockCard.direction == direction)
        # Cancelled rows are purged on cancel; hide any legacy soft-cancels.
        stmt = stmt.where(StockCard.finance_status != "cancelled")
        rows = (await self.session.scalars(stmt)).all()
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
            }
            for c in rows
        ]
