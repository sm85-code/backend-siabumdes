"""UU05 inventory catalog operations."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

import re

from sqlalchemy import Select, func, or_, select
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

CATEGORY_CODE_RE = re.compile(r"[A-Z0-9]{1,32}")
SKU_PREFIX = "KRS."


def expected_sku_prefix(category_code: str) -> str:
    return f"{SKU_PREFIX}{(category_code or '').upper()}-"


def validate_sku(sku: str, category_code: str) -> str:
    """SKU produk baru: KRS.<KODE KATEGORI>-<SUFIKS> (huruf besar)."""
    sku = (sku or "").strip().upper()
    prefix = expected_sku_prefix(category_code)
    suffix = sku[len(prefix):] if sku.startswith(prefix) else ""
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]*", suffix) or len(sku) > 64:
        raise ValueError(f"SKU harus berformat {prefix}<KODE>, contoh {prefix}001")
    return sku


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
        """Seed hanya kode default yang belum ada; nama yang sudah diubah admin tidak ditimpa."""
        uid = unit_usaha_id or (await self.resolve_uu05_unit()).id
        changed = False
        for code, name in DEFAULT_CATEGORIES:
            # Kode kategori unik global: kode yang sudah dipakai unit lain tidak dibuat ulang.
            if not await self.session.scalar(select(StockCategory.id).where(StockCategory.code == code)):
                self.session.add(StockCategory(code=code, name=name, unit_usaha_id=uid))
                changed = True
        if changed:
            await self.session.flush()
        return list(
            (
                await self.session.scalars(
                    select(StockCategory)
                    .where(StockCategory.unit_usaha_id == uid)
                    .order_by(StockCategory.created_at.asc(), StockCategory.code.asc())
                )
            ).all()
        )

    async def _category_has_products(self, category_id: str) -> bool:
        return bool(await self.session.scalar(select(Product.id).where(Product.category_id == category_id).limit(1)))

    async def _category_dict(self, r: StockCategory) -> dict[str, Any]:
        return {
            "id": r.id, "code": r.code, "name": r.name, "description": r.description or "",
            "unit_usaha_id": r.unit_usaha_id, "has_products": await self._category_has_products(r.id),
        }

    async def list_categories(self, unit_usaha_id: Optional[str] = None) -> list[dict[str, Any]]:
        return [await self._category_dict(r) for r in await self.ensure_categories(unit_usaha_id)]

    async def _check_category_clash(self, code: str, name: str, uid: str, exclude: Optional[str] = None) -> None:
        rows = (await self.session.scalars(select(StockCategory).where(
            or_(StockCategory.code == code, (StockCategory.unit_usaha_id == uid) & (func.lower(StockCategory.name) == name.lower()))
        ))).all()
        for row in rows:
            if row.id == exclude:
                continue
            if row.code == code:
                raise ValueError(f"Kode kategori {code} sudah dipakai")
            raise ValueError(f"Nama kategori {name} sudah dipakai")

    @staticmethod
    def _clean_category(code: str, name: str) -> tuple[str, str]:
        code, name = (code or "").strip().upper(), (name or "").strip()
        if not code or not name:
            raise ValueError("Kode dan nama kategori wajib diisi")
        if not CATEGORY_CODE_RE.fullmatch(code):
            raise ValueError("Kode kategori hanya huruf/angka (maks. 32 karakter)")
        return code, name

    async def create_category(self, *, code: str, name: str, description: Optional[str] = None, unit_usaha_id: Optional[str] = None) -> dict[str, Any]:
        uid = unit_usaha_id or (await self.resolve_uu05_unit()).id
        code, name = self._clean_category(code, name)
        await self._check_category_clash(code, name, uid)
        row = StockCategory(code=code, name=name, description=(description or "").strip() or None, unit_usaha_id=uid)
        self.session.add(row)
        await self.session.flush()
        return await self._category_dict(row)

    async def update_category(self, category_id: str, *, code: Optional[str] = None, name: Optional[str] = None, description: Optional[str] = None) -> dict[str, Any]:
        row = await self.session.get(StockCategory, category_id, with_for_update=True)
        if not row:
            raise ValueError("kategori tidak ditemukan")
        new_code, new_name = self._clean_category(code if code is not None else row.code, name if name is not None else row.name)
        if new_code != row.code and await self._category_has_products(row.id):
            raise ValueError("Kode kategori terkunci karena sudah dipakai produk")
        await self._check_category_clash(new_code, new_name, row.unit_usaha_id, exclude=row.id)
        row.code, row.name = new_code, new_name
        if description is not None:
            row.description = description.strip() or None
        await self.session.flush()
        return await self._category_dict(row)

    async def delete_category(self, category_id: str) -> None:
        row = await self.session.get(StockCategory, category_id, with_for_update=True)
        if not row:
            raise ValueError("kategori tidak ditemukan")
        if await self._category_has_products(row.id):
            raise ValueError("Kategori tidak dapat dihapus karena sudah dipakai produk")
        await self.session.delete(row)
        await self.session.flush()

    async def product_has_history(self, product_id: str) -> bool:
        return bool(
            await self.session.scalar(select(StockCard.id).where(StockCard.product_id == product_id, StockCard.finance_status != "cancelled").limit(1))
            or await self.session.scalar(select(StockAdjustment.id).where(StockAdjustment.product_id == product_id).limit(1))
        )

    def _product_query(
        self,
        *,
        unit_usaha_id: Optional[str] = None,
        category_id: Optional[str] = None,
        q: Optional[str] = None,
    ) -> Select[tuple[Product]]:
        stmt = select(Product).options(selectinload(Product.category)).order_by(Product.created_at.asc(), Product.name.asc())
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
        ids = [p.id for p in rows]
        history: set[str] = set()
        if ids:
            history = set((await self.session.scalars(select(StockCard.product_id).where(StockCard.product_id.in_(ids), StockCard.finance_status != "cancelled"))).all())
            history |= set((await self.session.scalars(select(StockAdjustment.product_id).where(StockAdjustment.product_id.in_(ids)))).all())
        return [{**self._product_dict(p), "has_history": p.id in history} for p in rows]

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
        if not name.strip():
            raise ValueError("SKU dan nama wajib diisi")
        sku_clean = validate_sku(sku, cat.code)
        # Stok awal hanya lewat Stock In (berjurnal); parameter lama diabaikan.
        opening_qty = 0
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
        changes_locked = (category_id is not None and category_id != product.category_id) or (
            cost_price is not None and Decimal(cost_price).quantize(Decimal("0.01")) != product.cost_price
        )
        if changes_locked and (product.qty_on_hand != 0 or await self.product_has_history(product.id)):
            raise ValueError("Kategori dan HPP terkunci karena produk sudah memiliki stok/riwayat mutasi")
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
        if product.qty_on_hand != 0 or await self.product_has_history(product.id):
            raise ValueError("Produk tidak dapat dihapus karena sudah memiliki stok/riwayat mutasi")
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
