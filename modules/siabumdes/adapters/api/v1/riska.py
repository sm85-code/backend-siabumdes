"""Deterministic, read-only financial assistant. No external model calls."""
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import REPORT_READ_LEVEL, public_role
from shared.database import get_db
from modules.siabumdes.adapters.api.deps import require_roles
from modules.siabumdes.adapters.api.scope import is_pengelola
from modules.siabumdes.application.reporting import ReportingService
from modules.siabumdes.identity.infrastructure.models import AiSessionPermission, ClosedPeriod, User
from modules.siabumdes.infrastructure.models import Account, UnitUsaha
from modules.siabumdes.money_json import stringify_money_fields

router = APIRouter(prefix="/api/riska", tags=["riska"])
READ = require_roles(*REPORT_READ_LEVEL)
DENIED = ("Akses ditolak. Tindakan operasional tutup buku, kunci periode, dan transfer bagi hasil "
          "mutlak hanya wewenang Admin Utama.")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    unit_usaha_id: str | None = None


async def available(session, user):
    if not is_pengelola(user):
        return True
    unit = await session.get(UnitUsaha, user.unit_usaha_id) if user.unit_usaha_id else None
    permission = await session.get(AiSessionPermission, unit.id) if unit else None
    return bool(permission and permission.is_allowed and unit and unit.active)


@router.get("/availability")
async def availability(user: User = Depends(READ), session: AsyncSession = Depends(get_db)):
    return {"enabled": await available(session, user)}


@router.get("/units")
async def units(user: User = Depends(READ), session: AsyncSession = Depends(get_db)):
    if not await available(session, user):
        raise HTTPException(403, "Penugasan Riska tidak aktif")
    stmt = select(UnitUsaha).where(UnitUsaha.active.is_(True)).order_by(UnitUsaha.code)
    if is_pengelola(user):
        stmt = stmt.where(UnitUsaha.id == user.unit_usaha_id)
    return [{"id": u.id, "code": u.code, "name": u.name} for u in (await session.scalars(stmt)).all()]


@router.post("/chat")
async def chat(body: ChatIn, user: User = Depends(READ), session: AsyncSession = Depends(get_db)):
    if not await available(session, user):
        raise HTTPException(403, "Penugasan Riska tidak aktif")
    message = body.message.lower().strip()
    if any(word in message for word in ("tutup buku", "kunci periode", "transfer bagi hasil")):
        return {"message": DENIED if public_role(user.role) != "admin" else
                "Gunakan halaman administrasi untuk tindakan ini. Riska tahap pertama hanya membaca data."}
    if any(word in message for word in ("input", "ubah", "hapus", "catat", "stock in", "stock out", "opname")):
        return {"message": "Input belum tersedia pada tahap pertama. Gunakan formulir operasional yang sudah ada."}
    if any(word in message for word in ("semua unit", "per unit", "per_unit", "konsolidasi", "lintas")):
        return {"message": "Laporan lintas entitas tidak tersedia. Pilih satu entitas; pembukuan tidak dikonsolidasi."}
    unit_id = body.unit_usaha_id
    if is_pengelola(user):
        if unit_id and unit_id != user.unit_usaha_id:
            raise HTTPException(403, "Akses unit lain ditolak")
        unit_id = user.unit_usaha_id
    unit = await session.get(UnitUsaha, unit_id) if unit_id else None
    if unit_id and (not unit or not unit.active):
        raise HTTPException(404, "Unit aktif tidak ditemukan")
    # Entity selection is structured; ambiguous natural-language references are never guessed.
    if re.search(r"\buu\d+\b", message) or "pusat" in message:
        return {"message": "Pilih entitas melalui pemilih di atas; jangan mencantumkan entitas lain dalam pesan."}
    if unit:
        from modules.siabumdes.adapters.api.v1.riska_execution import special_table
        table = await special_table(session, unit, message)
        if table:
            return table
    allowed = re.fullmatch(
        r"(?:berapa |lihat |tampilkan |cek )?(?:total )?(?:saldo(?: kas)?|kas|laba(?: rugi)?|rugi|pendapatan|performa)(?: bulan (?:ini|berjalan))?[?.!]?",
        message,
    )
    if not allowed:
        return {"message": "Gunakan: saldo kas, pendapatan, atau laba rugi bulan ini. Pilih entitas di pemilih."}
    today = datetime.now(ZoneInfo("Asia/Jakarta")).date()
    if re.search(r"\b(?:20\d{2}|januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember|kemarin|lalu|tahun)\b", message):
        return {"message": "Tahap pertama mendukung bulan berjalan. Gunakan laporan untuk periode lainnya."}
    start = date(today.year, today.month, 1)
    group = unit.code if unit else "BUMDES"
    label = unit.name if unit else "BUMDES Pusat"
    svc = ReportingService(session)
    if "kas" in message or "saldo" in message:
        stmt = select(Account).where(Account.group_code == group, Account.active.is_(True),
                                     Account.subcategory == "kas_bank")
        stmt = stmt.where(Account.unit_usaha_id == unit_id) if unit_id else stmt.where(Account.unit_usaha_id.is_(None))
        accounts = (await session.scalars(stmt)).all()
        rows = []
        for account in accounts:
            ledger = await svc.ledger(account.code, start, today, unit_id)
            rows.append({"akun": account.name, "saldo": ledger["saldo_akhir"]})
        return stringify_money_fields({"message": f"Saldo kas aktif {label} per {today.isoformat()}", "rows": rows})
    if any(word in message for word in ("laba", "rugi", "pendapatan", "performa")):
        data = await svc.laba_rugi(start, today, unit_id)
        closed = await session.scalar(select(ClosedPeriod.id).where(ClosedPeriod.period == today.strftime("%Y-%m"),
                                                                    ClosedPeriod.group_code == group))
        status = " (Periode sudah Tutup Buku bulanan)" if closed else ""
        rows = [{"metrik": k.replace("_", " "), "amount": data[k]}
                for k in ("total_pendapatan", "total_hpp", "total_beban", "laba_bersih")]
        return stringify_money_fields({"message": f"{label}, {start.isoformat()} sampai {today.isoformat()}{status}", "rows": rows})
    return {"message": "Saya dapat membaca saldo kas, pendapatan, dan laba rugi bulan berjalan untuk entitas terpilih."}
