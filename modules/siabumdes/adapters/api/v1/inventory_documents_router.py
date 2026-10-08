"""Unit 5 invoice, PO, and complete partner statements."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from modules.siabumdes.adapters.api.v1.uu05_inventory_router import require_inventory_user
from modules.siabumdes.adapters.api.scope import assert_can_mutate_period, assert_not_readonly, assert_unit_active
from modules.siabumdes.adapters.external.commercial_pdf import commercial_pdf
from modules.siabumdes.identity.application.services import get_org_profile, record_audit
from modules.siabumdes.infrastructure.models import UnitUsaha
from modules.siabumdes.inventory.application.transaction_labels import transaction_labels
from modules.siabumdes.inventory.application.services import InventoryService
from modules.siabumdes.inventory.application.documents import KINDS, build_snapshot, issue_document, sources
from modules.siabumdes.inventory.infrastructure.models import CommercialDocument, Customer, DocumentSource, Product, Vendor
from modules.siabumdes.report_branding import branding_from_org_profile
from shared.config import public_role
from shared.database import get_db

router = APIRouter(prefix="/api/v1/uu05_inventory/documents", tags=["inventory_documents"])
Money = Decimal


class Line(BaseModel):
    product_id: str
    date: date
    size: str = Field("", max_length=120)
    order_code: str = Field("", max_length=128)
    quantity: int = Field(ge=1, le=1000000)
    price: Money = Field(ge=0, max_digits=20, decimal_places=2)


class Allocation(BaseModel):
    source_id: str
    size: str = Field("", max_length=120)
    packing: Money = Field(Decimal(0), ge=0, max_digits=20, decimal_places=2)
    processing: Money = Field(Decimal(0), ge=0, max_digits=20, decimal_places=2)


class DocumentIn(BaseModel):
    kind: Literal["invoice", "po", "purchase_statement"]
    partner_id: str
    issued_date: date = Field(default_factory=lambda: datetime.now(ZoneInfo("Asia/Jakarta")).date())
    period_start: date
    period_end: date
    due_date: date | None = None
    payment_info: str = Field("", max_length=3000)
    source_ids: list[str] = Field(default_factory=list, max_length=500)
    lines: list[Line] = Field(default_factory=list, max_length=500)
    allocations: list[Allocation] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def dates_and_lines(self):
        if self.period_start > self.period_end:
            raise ValueError("Tanggal awal harus sebelum tanggal akhir")
        if self.due_date and self.due_date < self.issued_date:
            raise ValueError("Jatuh tempo tidak boleh sebelum tanggal dokumen")
        if self.kind == "po" and (not self.lines or self.source_ids or self.allocations):
            raise ValueError("PO memerlukan rincian produk tanpa transaksi sumber")
        if self.kind != "po" and (not self.source_ids or self.lines):
            raise ValueError("Invoice/rekap menggunakan transaksi sumber")
        if any(not self.period_start <= line.date <= self.period_end for line in self.lines):
            raise ValueError("Tanggal rincian harus berada dalam periode dokumen")
        return self


async def scoped_unit(session, user, write=False):
    if write:
        assert_not_readonly(user)
    unit = await session.scalar(
        select(UnitUsaha).where(UnitUsaha.code == "UU05").with_for_update()
        if write
        else select(UnitUsaha).where(UnitUsaha.code == "UU05")
    )
    if not unit:
        raise HTTPException(404, "Unit usaha 5 belum tersedia")
    if public_role(user.role) == "pengelola" and user.unit_usaha_id != unit.id:
        raise HTTPException(403, "Dokumen hanya untuk Unit 5")
    if write:
        await assert_unit_active(session, unit.id)
    return unit


def out(row):
    return {
        "id": row.id,
        "number": row.number,
        "kind": row.kind,
        "issued_date": row.issued_date.isoformat(),
        "status": row.status,
        **row.snapshot,
    }


async def pdf_response(snapshot, inline=False):
    blob = await run_in_threadpool(commercial_pdf, snapshot)
    filename = snapshot.get("number", "pratinjau").replace("/", "-") + ".pdf"
    return Response(
        blob,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/sources")
async def list_sources(
    kind: Literal["invoice", "purchase_statement"],
    partner_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    user=Depends(require_inventory_user),
    session=Depends(get_db),
):
    unit = await scoped_unit(session, user)
    if start and end and start > end:
        raise HTTPException(422, "Periode tidak valid")
    return await sources(session, kind, unit.id, partner_id, start, end)


@router.get("/catalog")
async def catalog(user=Depends(require_inventory_user), session=Depends(get_db)):
    unit = await scoped_unit(session, user)
    return [
        {"id": p.id, "name": p.name, "sku": p.sku, "unit_usaha_id": p.unit_usaha_id}
        for p in (
            await session.scalars(select(Product).where(Product.unit_usaha_id == unit.id).order_by(Product.name))
        ).all()
    ]


@router.get("/partners")
async def partners(
    kind: Literal["invoice", "purchase_statement"], user=Depends(require_inventory_user), session=Depends(get_db)
):
    unit = await scoped_unit(session, user)
    model = Customer if kind == "invoice" else Vendor
    return [
        {"id": r.id, "name": r.name, "address": r.address, "contact": r.contact}
        for r in (await session.scalars(select(model).where(model.unit_usaha_id == unit.id).order_by(model.name))).all()
    ]


@router.get("/statements")
async def statements(
    kind: Literal["invoice", "purchase_statement"],
    partner_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    user=Depends(require_inventory_user),
    session=Depends(get_db),
):
    unit = await scoped_unit(session, user)
    if start and end and start > end:
        raise HTTPException(422, "Periode tidak valid")
    model = KINDS[kind][1]
    mitra = {r.id: r for r in (await session.scalars(select(model).where(model.unit_usaha_id == unit.id))).all()}
    rows = await sources(session, kind, unit.id, partner_id, start, end)
    result = {}
    for row in rows:
        partner = mitra.get(row["partner_id"])
        if not partner:
            raise HTTPException(409, "Data mitra transaksi tidak sesuai unit")
        row["partner_name"] = partner.name
        item = result.setdefault(
            partner.id,
            {
                "id": partner.id,
                "name": partner.name,
                "total": Decimal(0),
                "paid": Decimal(0),
                "outstanding": Decimal(0),
                "overdue_count": 0,
                "rows": [],
            },
        )
        for key in ("total", "paid", "outstanding"):
            item[key] += Decimal(row[key])
        if (
            row["due_date"]
            and row["due_date"] < datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat()
            and Decimal(row["outstanding"]) > 0
        ):
            item["overdue_count"] += 1
        item["rows"].append(row)
    return [{**r, **{key: str(r[key]) for key in ("total", "paid", "outstanding")}} for r in result.values()]


@router.get("/statements/pdf")
async def statement_pdf(
    kind: Literal["invoice", "purchase_statement"],
    partner_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    user=Depends(require_inventory_user),
    session=Depends(get_db),
):
    groups = await statements(kind, partner_id, start, end, user, session)
    unit = await scoped_unit(session, user)
    branding = branding_from_org_profile(await get_org_profile(session))
    rows = [r for g in groups for r in g["rows"]]
    today = datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat()
    snapshot = {
        "kind": "statement",
        "number": "REKAP-" + ("PELANGGAN" if kind == "invoice" else "PEMASOK"),
        "org_name": branding.org_name,
        "address": branding.address_block,
        "logo_url": branding.logo_url,
        "unit_name": unit.name,
        "partner_name": groups[0]["name"] if partner_id and groups else "Seluruh mitra",
        "issued_date": today,
        "period_start": start.isoformat() if start else (min((r["date"] for r in rows), default=today)),
        "period_end": end.isoformat() if end else today,
        "items": rows,
        "total": str(sum((Decimal(g["total"]) for g in groups), Decimal(0))),
        "payment_info": "Nilai pembayaran dan sisa tagihan merupakan posisi terbaru untuk transaksi dalam periode terpilih. Pembelian/penjualan tunai sudah termasuk dibayar.",
    }
    return await pdf_response(snapshot)


class ReferencesIn(BaseModel):
    references: list[str] = Field(max_length=500)


@router.post("/transaction-links")
async def labels(body: ReferencesIn, user=Depends(require_inventory_user), session=Depends(get_db)):
    unit = await scoped_unit(session, user)
    return await transaction_labels(session, unit.id, body.references)


class PaymentLine(BaseModel):
    source_id: str
    amount: Money = Field(gt=0, max_digits=20, decimal_places=2)


class BatchPayment(BaseModel):
    kind: Literal["invoice", "purchase_statement"]
    partner_id: str
    paid_date: date
    allocations: list[PaymentLine] = Field(min_length=1, max_length=500)


@router.post("/payments")
async def pay_batch(body: BatchPayment, user=Depends(require_inventory_user), session=Depends(get_db)):
    unit = await scoped_unit(session, user, write=True)
    await assert_can_mutate_period(session, user, body.paid_date, unit.id)
    available = {r["id"]: r for r in await sources(session, body.kind, unit.id, body.partner_id)}
    if len({p.source_id for p in body.allocations}) != len(body.allocations):
        raise HTTPException(422, "Transaksi pembayaran tidak boleh berulang")
    for allocation in body.allocations:
        row = available.get(allocation.source_id)
        if not row or allocation.amount > Decimal(row["outstanding"]):
            raise HTTPException(422, "Nominal melebihi sisa tagihan atau transaksi tidak sesuai mitra/unit")
    svc = InventoryService(session)
    result = []
    try:
        for allocation in body.allocations:
            if body.kind == "invoice":
                result.append(
                    await svc.pay_sale(
                        sale_id=allocation.source_id,
                        amount=allocation.amount,
                        paid_date=body.paid_date,
                        debit_account_code="1.1.01.15",
                        credit_account_code="1.1.03.15",
                        unit_usaha_id=unit.id,
                        created_by=user.username,
                    )
                )
            else:
                result.append(
                    await svc.pay_purchase(
                        purchase_id=allocation.source_id,
                        amount=allocation.amount,
                        paid_date=body.paid_date,
                        debit_account_code="2.1.01.15",
                        credit_account_code="1.1.01.15",
                        unit_usaha_id=unit.id,
                        created_by=user.username,
                    )
                )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await record_audit(
        session,
        actor=user,
        action="batch_inventory_payment",
        entity="inventory_payments",
        entity_id=body.partner_id,
        detail=f"{len(result)} tagihan; {body.paid_date}",
    )
    return {"paid": len(result), "items": result}


@router.get("")
async def list_documents(
    kind: Literal["invoice", "po", "purchase_statement"] | None = None,
    offset: int = Query(0, ge=0),
    user=Depends(require_inventory_user),
    session=Depends(get_db),
):
    unit = await scoped_unit(session, user)
    stmt = (
        select(CommercialDocument)
        .where(CommercialDocument.unit_usaha_id == unit.id)
        .order_by(CommercialDocument.created_at.desc(), CommercialDocument.id)
        .offset(offset)
        .limit(51)
    )
    if kind:
        stmt = stmt.where(CommercialDocument.kind == kind)
    rows = (await session.scalars(stmt)).all()
    return {"items": [out(r) for r in rows[:50]], "has_more": len(rows) > 50}


@router.post("/preview")
async def preview(body: DocumentIn, user=Depends(require_inventory_user), session=Depends(get_db)):
    unit = await scoped_unit(session, user)
    snapshot = await build_snapshot(session, body, unit, branding_from_org_profile(await get_org_profile(session)))
    return await pdf_response(snapshot, inline=True)


@router.post("", status_code=201)
async def issue(body: DocumentIn, user=Depends(require_inventory_user), session=Depends(get_db)):
    unit = await scoped_unit(session, user, write=True)
    row = await issue_document(session, body, unit, branding_from_org_profile(await get_org_profile(session)), user)
    await record_audit(
        session,
        actor=user,
        action="issue_inventory_document",
        entity="inventory_documents",
        entity_id=row.id,
        detail=row.number,
    )
    return out(row)


async def get_document(doc_id, user, session):
    unit = await scoped_unit(session, user)
    row = await session.get(CommercialDocument, doc_id)
    if not row or row.unit_usaha_id != unit.id:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    return row


@router.get("/{doc_id}")
async def document_detail(doc_id: str, user=Depends(require_inventory_user), session=Depends(get_db)):
    row = await get_document(doc_id, user, session)
    data = out(row)
    if row.kind != "po":
        live = {r["id"]: r for r in await sources(session, row.kind, row.unit_usaha_id, row.partner_id)}
        data["payments"] = [live[i["source_id"]] for i in row.snapshot["items"] if i["source_id"] in live]
    return data


@router.get("/{doc_id}/pdf")
async def document_pdf(
    doc_id: str, inline: bool = False, user=Depends(require_inventory_user), session=Depends(get_db)
):
    row = await get_document(doc_id, user, session)
    data = {**row.snapshot, "status": row.status}
    if row.kind != "po":
        current = await document_detail(doc_id, user, session)
        paid = sum((Decimal(r["paid"]) for r in current["payments"]), Decimal(0))
        outstanding = sum((Decimal(r["outstanding"]) for r in current["payments"]), Decimal(0))
        data.update(paid=str(paid), outstanding=str(outstanding))
    return await pdf_response(data, inline)


@router.post("/{doc_id}/void")
async def void(doc_id: str, user=Depends(require_inventory_user), session=Depends(get_db)):
    await scoped_unit(session, user, write=True)
    row = await get_document(doc_id, user, session)
    row.status = "void"
    await session.execute(delete(DocumentSource).where(DocumentSource.document_id == row.id))
    await record_audit(
        session,
        actor=user,
        action="void_inventory_document",
        entity="inventory_documents",
        entity_id=row.id,
        detail=row.number,
    )
    return out(row)
