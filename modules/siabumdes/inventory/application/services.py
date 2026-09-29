"""UU05 inventory application services: catalog, movements, adjustments, reports."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.public_service import SiabumdesPublicService
from modules.siabumdes.inventory.application.catalog import (
    DEFAULT_CATEGORIES,
    INVENTORY_BUSINESS_TYPES,
    InventoryCatalogMixin,
    UU05_CODE,
)
from modules.siabumdes.inventory.application.stock_mixin import InventoryStockMixin
from modules.siabumdes.inventory.application.adjust_reports_mixin import InventoryAdjustReportsMixin
from modules.siabumdes.inventory.application.trade_mixin import InventoryTradeMixin

__all__ = ["InventoryService", "UU05_CODE", "INVENTORY_BUSINESS_TYPES", "DEFAULT_CATEGORIES"]


class InventoryService(
    InventoryCatalogMixin,
    InventoryStockMixin,
    InventoryAdjustReportsMixin,
    InventoryTradeMixin,
):
    def __init__(
        self,
        session: AsyncSession,
        finance: Optional[SiabumdesPublicService] = None,
    ) -> None:
        self.session = session
        self.finance = finance or SiabumdesPublicService(session)
