"""UU05 inventory catalog operations."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import selectinload

from modules.siabumdes.infrastructure.models import UnitUsaha
from modules.siabumdes.inventory.infrastructure.models import Product, StockAdjustment, StockCard, StockCategory

# Sejarah: modul ini awalnya "inventory UU05" dengan kode unit di-hardcode.
# UU05_CODE dipertahankan sebagai fallback kompatibilitas (dipakai di tempat
# yang belum sempat diaudit), tapi resolve_uu05_unit() sekarang mencari unit
# lewat business_type (perdagangan/manufaktur) supaya unit dagang baru selain
# UU05 otomatis ikut terhubung tanpa perlu ubah kode.
UU05_CODE = "UU05"
INVENTORY_BUSINESS_TYPES = ("perdagangan", "manufaktur")

DEFAULT_CATEGORIES: list[tuple[str, str]] = [
    ("PRT", "Peralatan Rumah Tangga"),
    ("PD", "Peralatan Dapur"),
    ("MM", "Makanan & Minuman"),
    ("FUR", "Furniture"),
    ("PHP", "Perawatan Hewan Peliharaan"),
    ("LAI", "Lainnya"),
]


class InventoryCatalogMixin:
    async def resolve_uu05_unit(self) -> UnitUsaha:
        unit = await self.session.scalar(
            select(UnitUsaha)
            .where(UnitUsaha.business_type.in_(INVENTORY_BUSINESS_TYPES))
            .order_by(UnitUsaha.code.asc())
        )
        if not unit:
            # Fallback kompatibilitas untuk data lama yang belum sempat diberi
            # business_type (mis. UU05 masih default 'jasa' sebelum di-backfill).
            unit = await self.session.scalar(select(UnitUsaha).where(UnitUsaha.code == UU05_CODE))
        if not unit:
            raise ValueError("Belum ada unit usaha berjenis Perdagangan/Manufaktur yang terdaftar")
        return unit

    async def ensure_categories(self, unit_usaha_id: Optional[str] = None) -> list[StockCategory]:
        if unit_usaha_id:
            uid = unit_usaha_id
        else:
            uid = (await self.resolve_uu05_unit()).id
        existing = (
            await self.session.scalars(select(StockCategory).where(StockCategory.unit_usaha_id == uid))
        ).all()
        by_code = {c.code: c for c in existing}
        changed = False
        for code, name in DEFAULT_CATEGORIES:
            if code in by_code:
                if by_code[code].name != name:
                    by_code[code].name = name
                    changed = True
                continue
            global_row = await self.session.scalar(select(StockCategory).where(StockCategory.code == code))
            if global_row:
                by_code[code] = global_row
                continue
            row = StockCategory(code=code, name=name, unit_usaha_id=uid)
            self.session.add(row)
            by_code[code] = row
            changed = True
        if changed:
            await self.session.flush()
        return [by_code[code] for code, _ in DEFAULT_CATEGORIES if code in by_code]

    async def list_categories(self) -> list[dict[str, Any]]:
        rows = await self.ensure_categories()
        return [{"id": r.id, "code": r.code, "name": r.name, "unit_usaha_id": r.unit_usaha_id} for r in rows]

    def _product_query(
        self,
        *,
        unit_usaha_id: Optional[str] = None,
        category_id: Optional[str] = None,
        q: Optional[str] = None,
    ) -> Select[tuple[Product]]:
        stmt = select(Product).options(selectinload(Product.category)).order_by(Product.name.asc())
        if unit_usaha_id:
            stmt = stmt.where(Product.unit_usaha_id == unit_usaha_id)
        if category_id:
            stmt = stmt.where(Product.category_id == category_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Product.sku.ilike(like), Product.name.ilike(like)))
        return stmt

    @staticmethod
    def _product_dict(p: Product) -> dict[str, Any]:
        return {
            "id": p.id,
            "sku": p.sku,
            "name": p.name,
            "category_id": p.category_id,
            "category_code": p.category.code if p.category else None,
            "category_name": p.category.name if p.category else None,
            "unit_usaha_id": p.unit_usaha_id,
            "unit_of_measure": p.unit_of_measure,
            "cost_price": str(p.cost_price),
            "sell_price": str(p.sell_price),
            "qty_on_hand": p.qty_on_hand,
            "stock_value": str((p.cost_price * Decimal(p.qty_on_hand)).quantize(Decimal("0.01"))),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }

    async def list_products(
        self,
        *,
        unit_usaha_id: Optional[str] = None,
        category_id: Optional[str] = None,
        q: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        await self.ensure_categories()
        rows = (
            await self.session.scalars(
                self._product_query(unit_usaha_id=unit_usaha_id, category_id=category_id, q=q)
            )
        ).all()
        return [self._product_dict(p) for p in rows]

    async def create_product(
        self,
        *,
        sku: str,
        name: str,
        category_id: str,
        unit_usaha_id: Optional[str] = None,
        unit_of_measure: str = "pcs",
        cost_price: Decimal = Decimal("0"),
        sell_price: Decimal = Decimal("0"),
        opening_qty: int = 0,
    ) -> dict[str, Any]:
        unit = await self.resolve_uu05_unit()
        uid = unit_usaha_id or unit.id
        await self.ensure_categories(uid)
        cat = await self.session.get(StockCategory, category_id)
        if not cat:
            raise ValueError("kategori tidak ditemukan")
        sku_clean = sku.strip().upper()
        if not sku_clean or not name.strip():
            raise ValueError("SKU dan nama wajib diisi")
        if opening_qty < 0:
            raise ValueError("qty awal tidak boleh negatif")
        exists = await self.session.scalar(
            select(Product).where(Product.sku == sku_clean, Product.unit_usaha_id == uid)
        )
        if exists:
            raise ValueError(f"SKU {sku_clean} sudah ada")
        product = Product(
            sku=sku_clean,
            name=name.strip(),
            category_id=category_id,
            unit_usaha_id=uid,
            unit_of_measure=(unit_of_measure or "pcs").strip() or "pcs",
            cost_price=Decimal(cost_price).quantize(Decimal("0.01")),
            sell_price=Decimal(sell_price).quantize(Decimal("0.01")),
            qty_on_hand=opening_qty,
        )
        self.session.add(product)
        await self.session.flush()
        await self.session.refresh(product, attribute_names=["category"])
        return self._product_dict(product)

    async def update_product(
        self,
        product_id: str,
        *,
        name: Optional[str] = None,
        category_id: Optional[str] = None,
        unit_of_measure: Optional[str] = None,
        cost_price: Optional[Decimal] = None,
        sell_price: Optional[Decimal] = None,
    ) -> dict[str, Any]:
        product = await self.session.scalar(
            select(Product).options(selectinload(Product.category)).where(Product.id == product_id)
        )
        if not product:
            raise ValueError("produk tidak ditemukan")
        if name is not None:
            product.name = name.strip()
        if category_id is not None:
            cat = await self.session.get(StockCategory, category_id)
            if not cat:
                raise ValueError("kategori tidak ditemukan")
            product.category_id = category_id
        if unit_of_measure is not None:
            product.unit_of_measure = unit_of_measure.strip() or product.unit_of_measure
        if cost_price is not None:
            product.cost_price = Decimal(cost_price).quantize(Decimal("0.01"))
        if sell_price is not None:
            product.sell_price = Decimal(sell_price).quantize(Decimal("0.01"))
        await self.session.flush()
        await self.session.refresh(product, attribute_names=["category"])
        return self._product_dict(product)

    async def delete_product(self, product_id: str) -> None:
        product = await self.session.get(Product, product_id)
        if not product:
            raise ValueError("produk tidak ditemukan")
        if product.qty_on_hand != 0:
            raise ValueError(
                "stok harus 0 sebelum produk dihapus — batalkan stock-in/out tersisa dulu"
            )
        # Delete children first. ORM delete of Product alone tries to SET NULL
        # product_id on stock_adjustments/stock_cards (NOT NULL) despite DB CASCADE.
        for adj in (
            await self.session.scalars(
                select(StockAdjustment).where(StockAdjustment.product_id == product_id)
            )
        ).all():
            # Adjustments may have posted finance journals (stock-adj:<id>, or the
            # reduce/loss pair for a loss adjustment).
            await self.finance.cancel_inventory_journal(f"stock-adj:{adj.id}")
            await self.finance.cancel_inventory_journal(f"stock-adj:{adj.id}:reduce")
            await self.finance.cancel_inventory_journal(f"stock-adj:{adj.id}:loss")
            await self.session.delete(adj)
        for card in (
            await self.session.scalars(select(StockCard).where(StockCard.product_id == product_id))
        ).all():
            if card.finance_status == "posted" and card.reference:
                await self.finance.cancel_inventory_journal(card.reference)
            await self.session.delete(card)
        await self.session.flush()
        await self.session.delete(product)
        await self.session.flush()
