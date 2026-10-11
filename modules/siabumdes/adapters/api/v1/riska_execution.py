"""Deterministic operational drafts with scoped validation and one-time confirmation."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import shlex

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.adapters.api.scope import assert_can_mutate_period, is_pengelola
from modules.siabumdes.adapters.api.v1.yield_router import PaymentIn
from modules.siabumdes.application.yield_transactions import lock_partner, save_linked_payment, find_payment, TYPE_CODE
from modules.siabumdes.identity.application.services import record_audit
from modules.siabumdes.identity.infrastructure.models import AiSessionPermission, RiskaDraft, User
from modules.siabumdes.infrastructure.models import Account, UnitUsaha, TransactionType
from modules.siabumdes.inventory.infrastructure.models import Product, Vendor, Customer
from modules.siabumdes.inventory.application.services import InventoryService
from modules.siabumdes.public_service import SiabumdesPublicService

router = APIRouter(prefix="/api/riska", tags=["riska"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["riska-permissions"])


class DraftIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class PermissionPatch(BaseModel):
    unit_usaha_id: str
    is_allowed: bool


@admin_router.get("/riska-permissions")
async def permissions(admin: User = Depends(require_roles("admin")), session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(UnitUsaha, AiSessionPermission.is_allowed).outerjoin(
        AiSessionPermission, AiSessionPermission.unit_usaha_id == UnitUsaha.id).order_by(UnitUsaha.code))).all()
    return [{"unit_usaha_id": u.id, "code": u.code, "name": u.name, "active": u.active,
             "is_allowed": bool(allowed)} for u, allowed in rows]


@admin_router.patch("/riska-permissions")
async def patch_permission(body: PermissionPatch, admin: User = Depends(require_roles("admin")),
                           session: AsyncSession = Depends(get_db)):
    unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.id == body.unit_usaha_id).with_for_update())
    if not unit:
        raise HTTPException(404, "Unit tidak ditemukan")
    await session.execute(insert(AiSessionPermission).values(unit_usaha_id=unit.id, is_allowed=body.is_allowed)
                          .on_conflict_do_update(index_elements=["unit_usaha_id"], set_={"is_allowed": body.is_allowed}))
    await record_audit(session, actor=admin, action="riska_unit_permission", entity="ai_session_permissions",
                       entity_id=unit.id, detail=f"is_allowed={body.is_allowed}")
    return {"unit_usaha_id": unit.id, "is_allowed": body.is_allowed}


async def manager_unit(session, user):
    # Lock assignment owner so admin revocation and execution have a serial order.
    unit = await session.scalar(select(UnitUsaha).where(UnitUsaha.id == user.unit_usaha_id).with_for_update())
    permission = await session.get(AiSessionPermission, user.unit_usaha_id) if unit else None
    if not is_pengelola(user) or not unit or not unit.active or not permission or not permission.is_allowed:
        raise HTTPException(403, "Penugasan Riska/unit tidak aktif")
    return unit


def parse_operation(message):
    try:
        tokens = shlex.split(message)
        action = tokens.pop(0).lower()
        data = {}
        for token in tokens:
            key, value = token.split("=", 1)
            if key in data:
                raise ValueError("Parameter ganda")
            data[key] = value
        allowed = {
            "imbal": {"mitra", "tanggal", "bulan", "tahun", "nominal"},
            "masuk": {"produk", "qty", "harga", "pemasok", "tanggal"},
            "keluar": {"produk", "qty", "harga", "customer", "tanggal"},
            "rusak": {"produk", "qty", "tanggal"},
        }
        if action not in allowed or set(data) != allowed[action]:
            raise ValueError("Parameter tidak lengkap/tidak dikenal")
        date.fromisoformat(data["tanggal"])
        data["action"] = action
        return data
    except (ValueError, IndexError):
        raise HTTPException(422, "Gunakan format lengkap: imbal mitra=ID tanggal=YYYY-MM-DD bulan=1 tahun=2026 nominal=100; "
                            "masuk produk=SKU qty=1 harga=100 pemasok=ID tanggal=YYYY-MM-DD; "
                            "keluar produk=SKU qty=1 harga=150 customer=ID tanggal=YYYY-MM-DD; "
                            "rusak produk=SKU qty=1 tanggal=YYYY-MM-DD") from None


async def account(session, unit, code=None, category=None, subcategory=None):
    stmt = select(Account).where(Account.group_code == unit.code, Account.unit_usaha_id == unit.id,
                                  Account.active.is_(True))
    if code:
        stmt = stmt.where(Account.code == code)
    if category:
        stmt = stmt.where(Account.category == category)
    if subcategory:
        stmt = stmt.where(Account.subcategory == subcategory)
    rows = (await session.scalars(stmt.order_by(Account.code).with_for_update())).all()
    if len(rows) != 1:
        raise HTTPException(422, "COA aktif harus tunggal dan sesuai unit; periksa konfigurasi akun operasional")
    return rows[0]


def money(value):
    try:
        result = Decimal(value)
        if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -2 or result >= Decimal('1e16'):
            raise ValueError()
        return result
    except (ValueError, ArithmeticError):
        raise HTTPException(422, "Nominal harus positif, maksimal dua desimal") from None


async def prepare(session, user, unit, data):
    when = date.fromisoformat(data["tanggal"])
    await assert_can_mutate_period(session, user, when, unit.id)
    action = data["action"]
    rows = []
    payload = dict(data)
    if action == "imbal":
        if unit.code != "UU04":
            raise HTTPException(403, "Imbal hasil hanya untuk UU04")
        partner = await lock_partner(session, data["mitra"], unit)
        body = PaymentIn(transaction_date=when, year=int(data["tahun"]), month=int(data["bulan"]), amount=money(data["nominal"]))
        if await find_payment(session, partner, body.year, body.month):
            raise HTTPException(409, "Pembayaran bulan ini sudah ada; ubah melalui halaman Imbal Hasil")
        kind = await session.scalar(select(TransactionType).where(TransactionType.group_code == unit.code,
                                                                  TransactionType.code == TYPE_CODE))
        if not kind:
            raise HTTPException(422, "Jenis transaksi imbal hasil belum dikonfigurasi")
        debit = await account(session, unit, code=kind.debit, subcategory="kas_bank")
        credit = await account(session, unit, code=kind.credit, category="pendapatan")
        rows.append({"debit": debit.code, "kredit": credit.code, "amount": str(body.amount)})
        label = partner.name
    else:
        if unit.business_type not in {"perdagangan", "manufaktur"}:
            raise HTTPException(403, "Mutasi stok hanya untuk unit perdagangan/manufaktur")
        product = await session.scalar(select(Product).where(Product.sku == data["produk"],
                                       Product.unit_usaha_id == unit.id).with_for_update())
        if not product:
            raise HTTPException(404, "SKU milik unit tidak ditemukan")
        qty = int(data["qty"])
        if qty <= 0 or qty > 100000000:
            raise HTTPException(422, "Qty harus positif dan dalam batas")
        if action != "masuk" and qty > product.qty_on_hand:
            raise HTTPException(422, "Stok tidak cukup")
        payload["product_id"] = product.id
        label = product.name
        inventory = await account(session, unit, category="aset", subcategory="aset_lancar",
                                  code=f"1.1.05.1{unit.code[-1]}" if unit.code in {"UU05", "UU06"} else None)
        # Canonical unit suffixes are 15/16, while new units use their own unambiguous COA.
        payload["inventory"] = inventory.code
        if action in {"masuk", "keluar"}:
            price = money(data["harga"])
            cash = await account(session, unit, subcategory="kas_bank",
                                 code=f"1.1.01.1{unit.code[-1]}" if unit.code in {"UU05", "UU06"} else None)
            payload["cash"] = cash.code
            model, key = (Vendor, "pemasok") if action == "masuk" else (Customer, "customer")
            party = await session.scalar(select(model).where(model.id == data[key], model.unit_usaha_id == unit.id))
            if not party:
                raise HTTPException(404, "Mitra operasional milik unit tidak ditemukan")
            if action == "masuk":
                rows.append({"debit": inventory.code, "kredit": cash.code, "amount": str((price * qty).quantize(Decimal('.01')))})
            else:
                hpp = await account(session, unit, category="hpp", subcategory="hpp_barang_dagangan",
                                    code=f"5.1.01.1{unit.code[-1]}" if unit.code in {"UU05", "UU06"} else None)
                revenue = await account(session, unit, category="pendapatan", subcategory="pendapatan_operasional",
                                        code=f"4.1.01.1{unit.code[-1]}" if unit.code in {"UU05", "UU06"} else None)
                payload.update(hpp=hpp.code, revenue=revenue.code)
                rows.extend([{"debit": hpp.code, "kredit": inventory.code, "amount": str((product.cost_price * qty).quantize(Decimal('.01')))},
                             {"debit": cash.code, "kredit": revenue.code, "amount": str((price * qty).quantize(Decimal('.01')))}])
        else:
            # Adapt the existing adjustment service's UU05 constants to the selected unit's COA.
            transit = await account(session, unit, code="1.1.05.51")
            loss = await account(session, unit, code="6.2.99.52", category="beban")
            payload.update(transit=transit.code, loss=loss.code)
            amount = str((product.cost_price * qty).quantize(Decimal('.01')))
            rows.extend([{"debit": transit.code, "kredit": inventory.code, "amount": amount},
                         {"debit": loss.code, "kredit": transit.code, "amount": amount}])
    for posting in rows:
        money(posting["amount"])
    return payload, {"unit": unit.name, "operation": action, "item": label, "tanggal": data["tanggal"], "details": {k: v for k, v in data.items() if k in {"qty", "harga", "bulan", "tahun", "nominal"}}, "rows": rows}


class ScopedFinance(SiabumdesPublicService):
    def __init__(self, session, unit, payload):
        super().__init__(session)
        self.unit = unit
        self.payload = payload

    async def record_inventory_journal(self, **kwargs):
        await account(self.session, self.unit, code=kwargs["debit_account_code"])
        await account(self.session, self.unit, code=kwargs["credit_account_code"])
        return await super().record_inventory_journal(**kwargs)


@router.post("/drafts")
async def draft(body: DraftIn, user: User = Depends(require_roles("pengelola")), session: AsyncSession = Depends(get_db)):
    unit = await manager_unit(session, user)
    try:
        payload, preview = await prepare(session, user, unit, parse_operation(body.message))
    except (ValueError, ArithmeticError):
        raise HTTPException(422, "Format tanggal, bulan, tahun atau qty tidak valid") from None
    row = RiskaDraft(user_id=user.id, unit_usaha_id=unit.id, payload=payload, preview=preview,
                     expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    session.add(row)
    await session.flush()
    return {"draft_id": row.id, "message": "Periksa draf dan pasangan jurnal sebelum konfirmasi.",
            "preview": preview, "expires_at": row.expires_at.isoformat()}


@router.post("/drafts/{draft_id}/execute")
async def execute(draft_id: str, user: User = Depends(require_roles("pengelola")), session: AsyncSession = Depends(get_db)):
    unit = await manager_unit(session, user)
    row = await session.scalar(select(RiskaDraft).where(RiskaDraft.id == draft_id, RiskaDraft.user_id == user.id,
                               RiskaDraft.unit_usaha_id == unit.id).with_for_update())
    if not row:
        raise HTTPException(404, "Draf tidak ditemukan")
    if row.consumed or row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(409, "Draf sudah digunakan/dibatalkan/kedaluwarsa")
    try:
        payload, preview = await prepare(session, user, unit, row.payload)
        if preview != row.preview or payload != row.payload:
            raise HTTPException(409, "Data/COA berubah; buat dan periksa draf baru")
        action = payload["action"]
        when = date.fromisoformat(payload["tanggal"])
        if action == "imbal":
            partner = await lock_partner(session, payload["mitra"], unit)
            body = PaymentIn(transaction_date=when, year=int(payload["tahun"]), month=int(payload["bulan"]), amount=money(payload["nominal"]))
            await save_linked_payment(session, user, unit, partner, body, body.amount)
        else:
            service = InventoryService(session, ScopedFinance(session, unit, payload))
            common = dict(product_id=payload["product_id"], unit_usaha_id=unit.id, created_by=user.id)
            qty = int(payload["qty"])
            if action == "masuk":
                await service.stock_in(**common, quantity=qty, unit_cost=money(payload["harga"]), movement_date=when,
                                       vendor_id=payload["pemasok"], debit_account_code=payload["inventory"], credit_account_code=payload["cash"])
            elif action == "keluar":
                await service.stock_out(**common, quantity=qty, sell_price=money(payload["harga"]), movement_date=when,
                    customer_id=payload["customer"], debit_account_code=payload["hpp"], credit_account_code=payload["inventory"],
                    revenue_debit_account_code=payload["cash"], revenue_credit_account_code=payload["revenue"])
            else:
                await service.adjust_stock(**common, quantity_delta=-qty, reason="rusak", adjustment_date=when,
                    inventory_account_code=payload["inventory"], transit_account_code=payload["transit"],
                    loss_account_code=payload["loss"])
        row.consumed = True
        await record_audit(session, actor=user, action="riska_execute", entity="riska_drafts", entity_id=row.id,
                           detail=f"{unit.code}: {payload['action']}")
    except (ValueError, ArithmeticError):
        raise HTTPException(422, "Data operasional tidak valid; periksa COA dan stok") from None
    return {"message": "Transaksi dan jurnal berhasil dicatat pada unit Anda."}


@router.delete("/drafts/{draft_id}")
async def cancel(draft_id: str, user: User = Depends(require_roles("pengelola")), session: AsyncSession = Depends(get_db)):
    unit = await manager_unit(session, user)
    row = await session.scalar(select(RiskaDraft).where(RiskaDraft.id == draft_id, RiskaDraft.user_id == user.id,
                               RiskaDraft.unit_usaha_id == unit.id).with_for_update())
    if not row or row.consumed:
        raise HTTPException(409, "Draf tidak tersedia")
    row.consumed = True
    return {"message": "Draf dibatalkan."}


async def special_table(session, unit, message):
    from modules.siabumdes.infrastructure.models import YieldPartner, YieldPayment
    from sqlalchemy import func
    if unit.code == "UU04" and any(token in message for token in ("mitra", "imbal", "cicilan")):
        year = datetime.now(ZoneInfo("Asia/Jakarta")).year
        partners = (await session.scalars(select(YieldPartner).where(YieldPartner.unit_usaha_id == unit.id)
                                         .order_by(YieldPartner.name))).all()
        payments = (await session.scalars(select(YieldPayment).join(YieldPartner).where(
            YieldPartner.unit_usaha_id == unit.id, YieldPayment.year == year))).all()
        paid = {(p.partner_id, p.month): str(p.amount) for p in payments}
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des']
        rows = [{"ID Mitra": p.id, "Nama Mitra": p.name, "Modal": str(p.capital),
                 **{m: paid.get((p.id, i), 'Belum dicatat') for i, m in enumerate(months, 1)}} for p in partners]
        return {"message": f"Imbal hasil {unit.name}, tahun {year}. Modal register bukan saldo piutang.",
                "rows": rows, "money_columns": ['Modal', *months]}
    if unit.business_type in {"perdagangan", "manufaktur"} and any(t in message for t in ("stok", "persediaan", "produk")):
        stmt = select(Product).where(Product.unit_usaha_id == unit.id).order_by(Product.sku)
        if "tipis" in message or "kritis" in message:
            stmt = stmt.where(Product.qty_on_hand <= 5)
        products = (await session.scalars(stmt)).all()
        vendors = (await session.execute(select(Vendor.id, Vendor.name).where(Vendor.unit_usaha_id == unit.id))).all()
        customers = (await session.execute(select(Customer.id, Customer.name).where(Customer.unit_usaha_id == unit.id))).all()
        total = await session.scalar(select(func.coalesce(func.sum(Product.qty_on_hand * Product.cost_price), 0))
                                     .where(Product.unit_usaha_id == unit.id))
        return {"message": f"Persediaan {unit.name}. Nilai seluruh stok: Rp {total}. Qty ≤ 5 ditandai kritis.",
                "rows": [{"SKU": p.sku, "Produk": p.name, "Qty": p.qty_on_hand, "HPP": str(p.cost_price),
                          "Nilai": str(p.cost_price * p.qty_on_hand), "Status": 'Kritis' if p.qty_on_hand <= 5 else 'Normal'} for p in products],
                "money_columns": ['HPP', 'Nilai'], "valuation": str(total),
                "parties": {"pemasok": [{"id": v.id, "name": v.name} for v in vendors],
                            "customer": [{"id": c.id, "name": c.name} for c in customers]}}
    return None
