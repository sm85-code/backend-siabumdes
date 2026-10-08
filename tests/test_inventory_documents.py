import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1:5432/test")

from modules.siabumdes.adapters.api.v1.inventory_documents_router import DocumentIn, scoped_unit  # noqa: E402
from modules.siabumdes.adapters.external.commercial_pdf import commercial_pdf, rupiah  # noqa: E402
from modules.siabumdes.inventory.application import documents as service  # noqa: E402
from modules.siabumdes.inventory.application.descriptions import inventory_description  # noqa: E402


@pytest.fixture
def body():
    return DocumentIn(
        kind="invoice",
        partner_id="buyer",
        issued_date="2026-10-09",
        period_start="2026-10-01",
        period_end="2026-10-09",
        source_ids=["sale"],
    )


@pytest.fixture
def context():
    unit = SimpleNamespace(id="u5", code="UU05", name="Unit Perdagangan")
    partner = SimpleNamespace(id="buyer", unit_usaha_id="u5", name="Toko A", address="Alamat A", contact="08123")
    branding = SimpleNamespace(org_name="BUMDes Karya Raharja", address_block="Desa Wonoharjo", logo_url="")
    session = SimpleNamespace(
        get=AsyncMock(return_value=partner), scalar=AsyncMock(return_value=0), flush=AsyncMock(), add=lambda r: None
    )
    return session, unit, branding


@pytest.mark.asyncio
async def test_invoice_uses_recorded_amount_and_snapshots_identity(body, context, monkeypatch):
    session, unit, branding = context
    source = {
        "id": "sale",
        "name": "Partisi",
        "sku": "SKU",
        "quantity": 2,
        "unit": "pcs",
        "date": "2026-10-08",
        "total": "100000",
        "invoice_number": "INV-01",
        "issued": False,
    }
    monkeypatch.setattr(service, "sources", AsyncMock(return_value=[source]))
    body.allocations = []
    snapshot = await service.build_snapshot(session, body, unit, branding)
    assert snapshot["total"] == "100000"
    assert snapshot["items"][0]["name"] == "Partisi"
    assert snapshot["partner_name"] == "Toko A"
    assert snapshot["items"][0]["price"] == "50000.00"
    # No stock or financial writes during preview.
    session.flush.assert_not_awaited()
    partner = await session.get(None, None)
    partner.name = "New Name"
    assert snapshot["partner_name"] == "Toko A"


@pytest.mark.asyncio
async def test_invoice_rejects_duplicate_foreign_or_issued_sources(body, context, monkeypatch):
    session, unit, branding = context
    monkeypatch.setattr(service, "sources", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException) as error:
        await service.build_snapshot(session, body, unit, branding)
    assert error.value.status_code == 422
    body.source_ids = ["sale", "sale"]
    with pytest.raises(HTTPException):
        await service.build_snapshot(session, body, unit, branding)
    body.source_ids = ["sale"]
    monkeypatch.setattr(service, "sources", AsyncMock(return_value=[{"id": "sale", "issued": True}]))
    with pytest.raises(HTTPException) as error:
        await service.build_snapshot(session, body, unit, branding)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_fees_are_allocation_not_extra_revenue(body, context, monkeypatch):
    from modules.siabumdes.adapters.api.v1.inventory_documents_router import Allocation

    session, unit, branding = context
    source = {
        "id": "sale",
        "name": "Partisi",
        "sku": "SKU",
        "quantity": 1,
        "unit": "pcs",
        "date": "2026-10-08",
        "total": "100000",
        "invoice_number": "INV-01",
        "issued": False,
    }
    monkeypatch.setattr(service, "sources", AsyncMock(return_value=[source]))
    body.allocations = [Allocation(source_id="sale", packing="20000", processing="10000")]
    snapshot = await service.build_snapshot(session, body, unit, branding)
    assert snapshot["total"] == "100000"
    assert snapshot["items"][0]["goods"] == "70000"
    body.allocations[0].packing = Decimal("100000")
    with pytest.raises(HTTPException):
        await service.build_snapshot(session, body, unit, branding)


@pytest.mark.asyncio
async def test_po_does_not_create_stock_or_finance(context):
    session, unit, branding = context
    partner = await session.get(None, None)
    product = SimpleNamespace(id="p", unit_usaha_id="u5", name="Beras", sku="B", unit_of_measure="kg")
    session.get.side_effect = [partner, product]
    body = DocumentIn(
        kind="po",
        partner_id="supplier",
        period_start="2026-10-01",
        period_end="2026-10-09",
        lines=[{"product_id": "p", "date": "2026-10-08", "quantity": 5, "price": "12000"}],
    )
    snapshot = await service.build_snapshot(session, body, unit, branding)
    assert snapshot["total"] == "60000.00"
    assert snapshot["items"][0]["source_id"] is None
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_only_writes_document_and_source_links(body, context, monkeypatch):
    session, unit, branding = context
    snapshot = {"items": [{"source_id": "sale"}]}
    monkeypatch.setattr(service, "build_snapshot", AsyncMock(return_value=snapshot))
    added = []
    session.add = added.append
    row = await service.issue_document(session, body, unit, branding, SimpleNamespace(id="actor"))
    assert row.number == "INV/UU05/202610/0001"
    assert [type(r).__name__ for r in added] == ["CommercialDocument", "DocumentSource"]


@pytest.mark.asyncio
async def test_unit_scope_blocks_other_managers(context):
    session, unit, _ = context
    session.scalar.return_value = unit
    with pytest.raises(HTTPException) as error:
        await scoped_unit(session, SimpleNamespace(role="pengelola", unit_usaha_id="u4"))
    assert error.value.status_code == 403
    assert await scoped_unit(session, SimpleNamespace(role="pengelola", unit_usaha_id="u5")) is unit


@pytest.mark.parametrize(
    "changes", [{"period_end": "2026-09-30"}, {"due_date": "2026-01-01"}, {"kind": "po", "source_ids": ["sale"]}]
)
def test_document_validation(body, changes):
    with pytest.raises(ValidationError):
        DocumentIn.model_validate({**body.model_dump(), **changes})


def sample(kind="invoice", count=3):
    return {
        "kind": kind,
        "number": "INV/UU05/202610/0001",
        "org_name": "BUMDes Karya Raharja",
        "address": "Desa Wonoharjo - Kec. Pangandaran\nKab. Pangandaran, Jawa Barat",
        "logo_url": "",
        "unit_name": "Unit Usaha Perdagangan",
        "issued_date": "2026-10-09",
        "due_date": "2026-10-20",
        "period_start": "2026-10-01",
        "period_end": "2026-10-09",
        "partner_name": "Toko Sejahtera",
        "partner_address": "Desa Wonoharjo, Kecamatan Pangandaran",
        "payment_info": "Pembayaran melalui rekening unit usaha.\nMohon mencantumkan nomor invoice.",
        "total": str(755000 * count),
        "items": [
            {
                "date": "2026-10-08",
                "name": "Partisi Rak Palang",
                "size": "150 × 20 × 200 cm",
                "quantity": 1,
                "unit": "pcs",
                "price": "575000",
                "goods": "575000",
                "packing": "170000",
                "processing": "10000",
                "total": "755000",
                "order_code": "AZF.1R.RPL.14020200.1409F",
            }
            for _ in range(count)
        ],
    }


def test_pdf_and_money_preserve_totals_and_multiple_pages():
    assert rupiah("1234567.50") == "Rp 1.234.567,50"
    for kind in ("invoice", "po", "purchase_statement"):
        pdf = commercial_pdf(sample(kind, 45))
        assert pdf.startswith(b"%PDF")
        assert b"/Count 1\n" not in pdf
    assert (
        inventory_description("Pembelian Stok", partner="PT A", invoice="INV-1", product="Beras", quantity=10)
        == "Pembelian Stok · PT A · INV-1 · Beras ×10"
    )


def test_cash_and_partial_payment_balances_are_correct():
    cash = SimpleNamespace(total_amount=Decimal("100"), payment_method="cash", payments=[])
    credit = SimpleNamespace(
        total_amount=Decimal("100"), payment_method="credit", payments=[SimpleNamespace(amount=Decimal("30"))]
    )
    assert service.balances(cash) == (Decimal("100"), Decimal("100"), Decimal("0"))
    assert service.balances(credit) == (Decimal("100"), Decimal("30"), Decimal("70"))


@pytest.mark.asyncio
async def test_batch_payment_validates_all_lines_before_posting(monkeypatch, context):
    from modules.siabumdes.adapters.api.v1 import inventory_documents_router as router

    session, unit, _ = context
    actor = SimpleNamespace(id="actor", username="admin", name="Admin", role="admin")
    monkeypatch.setattr(router, "scoped_unit", AsyncMock(return_value=unit))
    guard = AsyncMock()
    monkeypatch.setattr(router, "assert_can_mutate_period", guard)
    monkeypatch.setattr(
        router, "sources", AsyncMock(return_value=[{"id": "a", "outstanding": "50"}, {"id": "b", "outstanding": "20"}])
    )
    finance = SimpleNamespace(pay_sale=AsyncMock())
    monkeypatch.setattr(router, "InventoryService", lambda _: finance)
    body = router.BatchPayment(
        kind="invoice",
        partner_id="buyer",
        paid_date="2026-10-09",
        allocations=[{"source_id": "a", "amount": "50"}, {"source_id": "b", "amount": "21"}],
    )
    with pytest.raises(HTTPException) as error:
        await router.pay_batch(body, actor, session)
    assert error.value.status_code == 422
    finance.pay_sale.assert_not_awaited()
    body.allocations[1].amount = Decimal("20")
    monkeypatch.setattr(router, "record_audit", AsyncMock())
    result = await router.pay_batch(body, actor, session)
    assert result["paid"] == 2
    assert finance.pay_sale.await_count == 2
    assert finance.pay_sale.await_args.kwargs["unit_usaha_id"] == "u5"
    guard.assert_awaited()
