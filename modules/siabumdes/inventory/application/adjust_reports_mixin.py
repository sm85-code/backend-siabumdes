"""UU05 inventory adjustments and reports."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from modules.siabumdes.inventory.application.descriptions import inventory_description
from modules.siabumdes.inventory.application.coa import validate_coa_codes
from modules.siabumdes.inventory.infrastructure.models import (
    Product,
    Purchase,
    StockAdjustment,
    StockCard,
)

PERSEDIAAN_ACCOUNT_CODE = "1.1.05.15"
PENYESUAIAN_NILAI_PERSEDIAAN_ACCOUNT_CODE = "1.1.05.51"
BEBAN_KERUGIAN_BARANG_ACCOUNT_CODE = "6.2.99.52"


class InventoryAdjustReportsMixin:
    async def adjust_stock(
        self,
        *,
        product_id: str,
        quantity_delta: int,
        reason: str,
        adjustment_date: date,
        notes: Optional[str] = None,
        unit_usaha_id: Optional[str] = None,
        debit_account_code: Optional[str] = None,
        credit_account_code: Optional[str] = None,
        created_by: str = "system-inventory",
        inventory_account_code: str = PERSEDIAAN_ACCOUNT_CODE,
        transit_account_code: str = PENYESUAIAN_NILAI_PERSEDIAAN_ACCOUNT_CODE,
        loss_account_code: str = BEBAN_KERUGIAN_BARANG_ACCOUNT_CODE,
    ) -> dict[str, Any]:
        if quantity_delta == 0:
            raise ValueError("selisih jumlah tidak boleh 0")
        reason_clean = (reason or "").strip().lower()
        if reason_clean not in {"rusak", "kadaluarsa", "koreksi"}:
            raise ValueError("alasan harus rusak, kadaluarsa, atau koreksi")
        product = await self.session.get(Product, product_id)
        if not product:
            raise ValueError("produk tidak ditemukan")
        new_qty = product.qty_on_hand + quantity_delta
        if new_qty < 0:
            raise ValueError("penyesuaian membuat stok negatif")

        amount = (product.cost_price * Decimal(abs(quantity_delta))).quantize(Decimal("0.01"))
        journal_reference = None
        finance_status = "skipped"
        is_loss = quantity_delta < 0

        if amount > 0 and not is_loss:
            if not (debit_account_code or "").strip() or not (credit_account_code or "").strip():
                raise ValueError(
                    "akun debit dan akun kredit wajib diisi "
                    "ketika penyesuaian berdampak nilai (HPP × |selisih| > 0)"
                )
            await validate_coa_codes(self.session, debit_account_code, credit_account_code)
        elif amount > 0 and is_loss:
            await validate_coa_codes(
                self.session,
                transit_account_code,
                inventory_account_code,
                loss_account_code,
            )

        product.qty_on_hand = new_qty
        adj = StockAdjustment(
            product_id=product_id,
            adjustment_date=adjustment_date,
            quantity_delta=quantity_delta,
            reason=reason_clean,
            notes=(notes or "").strip() or None,
        )
        self.session.add(adj)
        await self.session.flush()

        if amount > 0 and is_loss:
            uid = unit_usaha_id or product.unit_usaha_id
            journal_reference = f"stock-adj:{adj.id}"
            reduction_reference = f"stock-adj:{adj.id}:reduce"
            loss_reference = f"stock-adj:{adj.id}:loss"
            desc_base = inventory_description("Koreksi Stok" if reason_clean == "koreksi" else "Penyesuaian Stok", product=product.name, note=f"Berkurang {abs(quantity_delta)} · {reason_clean}")
            # 1) Pengurangan fisik nilai persediaan: Dr Penyesuaian Nilai Persediaan / Cr Persediaan
            await self.finance.record_inventory_journal(
                movement_date=adjustment_date,
                unit_usaha_id=uid,
                amount=amount,
                debit_account_code=transit_account_code,
                credit_account_code=inventory_account_code,
                description=f"{desc_base} · Pengurangan Persediaan",
                reference=reduction_reference,
                created_by=created_by,
                transaction_type="inventory_adjustment_reduction",
            )
            # 2) Pengakuan beban kerugian: Dr Beban Kerugian Barang / Cr Penyesuaian Nilai Persediaan
            await self.finance.record_inventory_journal(
                movement_date=adjustment_date,
                unit_usaha_id=uid,
                amount=amount,
                debit_account_code=loss_account_code,
                credit_account_code=transit_account_code,
                description=f"{desc_base} · Beban Kerugian",
                reference=loss_reference,
                created_by=created_by,
                transaction_type="inventory_adjustment_loss",
            )
            finance_status = "posted"
        elif amount > 0:
            uid = unit_usaha_id or product.unit_usaha_id
            journal_reference = f"stock-adj:{adj.id}"
            desc = inventory_description("Koreksi Stok" if reason_clean == "koreksi" else "Penyesuaian Stok", product=product.name, note=f"Bertambah {quantity_delta} · {reason_clean}")
            await self.finance.record_inventory_journal(
                movement_date=adjustment_date,
                unit_usaha_id=uid,
                amount=amount,
                debit_account_code=debit_account_code.strip(),
                credit_account_code=credit_account_code.strip(),
                description=desc,
                reference=journal_reference,
                created_by=created_by,
                transaction_type="inventory_adjustment",
            )
            finance_status = "posted"

        return {
            "id": adj.id,
            "product_id": product_id,
            "sku": product.sku,
            "adjustment_date": adj.adjustment_date.isoformat(),
            "quantity_delta": adj.quantity_delta,
            "reason": adj.reason,
            "notes": adj.notes,
            "qty_on_hand": product.qty_on_hand,
            "cost_impact": str(amount),
            "journal_reference": journal_reference,
            "finance_status": finance_status,
        }


    async def list_adjustments(
        self,
        *,
        limit: int = 100,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(StockAdjustment)
            .options(selectinload(StockAdjustment.product))
            .order_by(StockAdjustment.adjustment_date.desc(), StockAdjustment.created_at.desc())
            .limit(min(limit, 2000))
        )
        if date_from:
            stmt = stmt.where(StockAdjustment.adjustment_date >= date_from)
        if date_to:
            stmt = stmt.where(StockAdjustment.adjustment_date <= date_to)
        rows = (await self.session.scalars(stmt)).all()
        return [
            {
                "id": a.id,
                "product_id": a.product_id,
                "sku": a.product.sku if a.product else None,
                "product_name": a.product.name if a.product else None,
                "adjustment_date": a.adjustment_date.isoformat(),
                "quantity_delta": a.quantity_delta,
                "reason": a.reason,
                "notes": a.notes,
            }
            for a in rows
        ]

    async def cancel_adjustment(self, adjustment_id: str) -> None:
        """Reverse qty impact, remove finance journal(s), delete adjustment row."""
        row = await self.session.get(StockAdjustment, adjustment_id)
        if not row:
            # Product delete may have removed the row before journals were cleaned.
            await self.finance.cancel_inventory_journal(f"stock-adj:{adjustment_id}")
            await self.finance.cancel_inventory_journal(f"stock-adj:{adjustment_id}:reduce")
            await self.finance.cancel_inventory_journal(f"stock-adj:{adjustment_id}:loss")
            return
        product = await self.session.get(Product, row.product_id)
        if product:
            # Reverse the delta that was applied at create time.
            product.qty_on_hand = max(0, product.qty_on_hand - row.quantity_delta)
        # Gain adjustments post a single "stock-adj:<id>" journal; loss adjustments post
        # a "reduce" + "loss" pair instead — cancel whichever reference(s) exist.
        await self.finance.cancel_inventory_journal(f"stock-adj:{row.id}")
        await self.finance.cancel_inventory_journal(f"stock-adj:{row.id}:reduce")
        await self.finance.cancel_inventory_journal(f"stock-adj:{row.id}:loss")
        await self.session.delete(row)
        await self.session.flush()


    async def stock_valuation_report(self) -> dict[str, Any]:
        await self.ensure_categories()
        products = (
            await self.session.scalars(
                select(Product).options(selectinload(Product.category)).order_by(Product.name.asc())
            )
        ).all()
        lines = []
        total_qty = 0
        total_value = Decimal("0.00")
        by_category: dict[str, dict[str, Any]] = {}
        for p in products:
            value = (p.cost_price * Decimal(p.qty_on_hand)).quantize(Decimal("0.01"))
            total_qty += p.qty_on_hand
            total_value += value
            cat_name = p.category.name if p.category else "Lainnya"
            bucket = by_category.setdefault(
                cat_name, {"category": cat_name, "qty": 0, "value": Decimal("0.00"), "sku_count": 0}
            )
            bucket["qty"] += p.qty_on_hand
            bucket["value"] += value
            bucket["sku_count"] += 1
            lines.append(self._product_dict(p))
        return {
            "summary": {
                "sku_count": len(products),
                "total_qty": total_qty,
                "total_value": str(total_value),
            },
            "by_category": [
                {
                    "category": v["category"],
                    "qty": v["qty"],
                    "value": str(v["value"]),
                    "sku_count": v["sku_count"],
                }
                for v in by_category.values()
            ],
            "products": lines,
        }


    async def movement_summary_report(
        self,
        *,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> dict[str, Any]:
        stmt = select(StockCard).where(StockCard.finance_status != "cancelled")
        if date_from:
            stmt = stmt.where(StockCard.movement_date >= date_from)
        if date_to:
            stmt = stmt.where(StockCard.movement_date <= date_to)
        rows = (await self.session.scalars(stmt)).all()
        in_qty = sum(c.quantity for c in rows if c.direction == "in")
        out_qty = sum(c.quantity for c in rows if c.direction == "out")
        in_value = sum((c.total_value for c in rows if c.direction == "in"), Decimal("0.00"))
        out_value = sum((c.total_value for c in rows if c.direction == "out"), Decimal("0.00"))
        # Nilai jual: penjualan pakai harga jual, pemakaian internal tetap HPP.
        sale_values = await self._sale_line_values(rows)
        out_sale_value = sum(
            (sale_values.get(c.id, c.total_value) for c in rows if c.direction == "out"), Decimal("0.00")
        )
        return {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "stock_in": {
                "qty": in_qty,
                "value": str(in_value),
                "count": sum(1 for c in rows if c.direction == "in"),
            },
            "stock_out": {
                "qty": out_qty,
                "value": str(out_value),
                "sale_value": str(out_sale_value),
                "count": sum(1 for c in rows if c.direction == "out"),
            },
            "net_qty": in_qty - out_qty,
        }

    async def remaining_by_source_report(self, as_of: Optional[date] = None) -> dict[str, Any]:
        """Sisa stok per SKU menurut sumber pembelian (reguler vs Belanja Modal Awal).

        Atribusi FIFO: barang keluar memakai stok masuk terlama dulu, jadi stok
        tersisa (qty_on_hand) berasal dari Stock In terbaru. Stok masuk tanpa
        pembelian (atau sisa melebihi riwayat masuk) dihitung reguler.
        """
        products = (await self.session.scalars(select(Product))).all()
        on_hand = {p.id: p.qty_on_hand for p in products}
        if as_of:
            # Mundurkan stok saat ini ke akhir tanggal as_of.
            later = (await self.session.scalars(select(StockCard).where(
                StockCard.movement_date > as_of, StockCard.finance_status != "cancelled"))).all()
            for c in later:
                if c.product_id in on_hand:
                    on_hand[c.product_id] += -c.quantity if c.direction == "in" else c.quantity
            for a in (await self.session.scalars(select(StockAdjustment).where(StockAdjustment.adjustment_date > as_of))).all():
                if a.product_id in on_hand:
                    on_hand[a.product_id] -= a.quantity_delta
        products = [p for p in products if on_hand[p.id] > 0]
        if not products:
            return {"reguler": [], "modal_awal": []}
        stmt = select(StockCard).where(
            StockCard.product_id.in_([p.id for p in products]),
            StockCard.direction == "in",
            StockCard.finance_status != "cancelled",
        ).order_by(StockCard.movement_date.desc(), StockCard.created_at.desc())
        if as_of:
            stmt = stmt.where(StockCard.movement_date <= as_of)
        cards = (await self.session.scalars(stmt)).all()
        heads = {c.nota_id or c.id for c in cards}
        modal = set((await self.session.scalars(
            select(Purchase.stock_card_id).where(Purchase.stock_card_id.in_(heads), Purchase.payment_method == "modal_awal")
        )).all()) if heads else set()
        by_product: dict[str, list[StockCard]] = {}
        for c in cards:
            by_product.setdefault(c.product_id, []).append(c)
        out: dict[str, list[dict[str, Any]]] = {"reguler": [], "modal_awal": []}
        for p in products:
            left = on_hand[p.id]
            split = {"reguler": 0, "modal_awal": 0}
            for c in by_product.get(p.id, []):
                if left <= 0:
                    break
                take = min(left, c.quantity)
                split["modal_awal" if (c.nota_id or c.id) in modal else "reguler"] += take
                left -= take
            split["reguler"] += max(left, 0)
            for key, qty in split.items():
                if qty > 0:
                    out[key].append({
                        "product_id": p.id, "sku": p.sku, "name": p.name, "unit": p.unit_of_measure,
                        "qty": qty, "hpp": str(p.cost_price),
                        "value": str((p.cost_price * qty).quantize(Decimal("0.01"))),
                    })
        return out
