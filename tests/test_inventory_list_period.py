"""List Stock In/Out & Penyesuaian bisa difilter periode (date_from/date_to)."""
import asyncio
from datetime import date

from modules.siabumdes.inventory.application.services import InventoryService


class Session:
    def __init__(self):
        self.stmts = []

    async def scalars(self, stmt):
        self.stmts.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        return type("R", (), {"all": lambda self: []})()


def test_movements_and_adjustments_filter_by_period():
    s = Session()
    svc = InventoryService.__new__(InventoryService)
    svc.session = s
    asyncio.run(svc.list_movements(direction="in", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)))
    asyncio.run(svc.list_adjustments(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)))
    assert "movement_date >= '2026-10-01'" in s.stmts[0] and "movement_date <= '2026-10-31'" in s.stmts[0]
    assert "adjustment_date >= '2026-10-01'" in s.stmts[1]


def test_without_period_is_backward_compatible():
    s = Session()
    svc = InventoryService.__new__(InventoryService)
    svc.session = s
    asyncio.run(svc.list_movements())
    assert "movement_date >=" not in s.stmts[0]
