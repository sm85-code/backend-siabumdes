"""Satu sumber kebenaran untuk proporsi bagi hasil.

Sebelumnya rasio ini hardcode terpisah di closing.py (BUMDES_ALLOC, dipakai
saat memposting jurnal penutup -- satu-satunya yang menyentuh uang riil) dan
reporting.py (perubahan_ekuitas/per_unit, cuma untuk tampilan laporan) --
kalau salah satu diubah tanpa yang lain, laporan bisa menampilkan angka yang
tidak sama dengan yang sungguhan diposting ke jurnal.

Sekarang keduanya baca dari sini, yang baca baris OrgProfile (diedit lewat
menu Profil BUMDES). Nilai default sama persis dengan yang dulu hardcode,
supaya deployment yang belum pernah buka menu itu tidak berubah perilakunya.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.identity.application.services import get_org_profile
from modules.siabumdes.infrastructure.models import BagiHasilTransferShare, UnitBagiHasilShare

SHARE_FIELDS_BUMDES = (
    "share_pengurus",
    "share_penasihat",
    "share_pengawas",
    "share_dana_sosial",
    "share_pades",
    "share_modal_bumdes",
)
SHARE_FIELDS_UNIT = ("share_unit_pengelola", "share_unit_bumdes")

_HUNDRED = Decimal("100")
_TOLERANCE = Decimal("0.01")  # pembulatan Numeric(5,2)


@dataclass(frozen=True)
class BagiHasilConfig:
    """Semua persen dalam skala 0-100 (bukan 0-1)."""

    pengurus: Decimal
    penasihat: Decimal
    pengawas: Decimal
    dana_sosial: Decimal
    pades: Decimal
    modal_bumdes: Decimal
    unit_pengelola: Decimal
    unit_bumdes: Decimal

    @property
    def utang_bh_bumdes_pct(self) -> Decimal:
        """52% (default): porsi yang diposting sebagai kewajiban lancar."""
        return self.pengurus + self.penasihat + self.pengawas + self.dana_sosial

    @property
    def ekuitas_pct(self) -> Decimal:
        """48% (default): porsi yang langsung menambah ekuitas (PADes + cadangan)."""
        return self.pades + self.modal_bumdes

    def bumdes_alloc(self) -> tuple[tuple[str, Decimal], ...]:
        """Bentuk (slug, rasio 0-1) yang dipakai closing.py untuk posting jurnal.

        Urutan menentukan slug mana yang menampung sisa pembulatan (elemen
        terakhir) -- pola yang sama seperti BUMDES_ALLOC lama, sengaja
        dipertahankan supaya perilaku pembulatan closing.py tidak berubah.
        """
        from modules.siabumdes.application.closing import SUB_UTANG_BH_BUMDES
        from modules.siabumdes.coa_taxonomy import SUB_BAGI_HASIL_DESA, SUB_LABA_DICADANGKAN

        return (
            (SUB_UTANG_BH_BUMDES, self.utang_bh_bumdes_pct / _HUNDRED),
            (SUB_BAGI_HASIL_DESA, self.pades / _HUNDRED),
            (SUB_LABA_DICADANGKAN, self.modal_bumdes / _HUNDRED),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "share_pengurus": float(self.pengurus),
            "share_penasihat": float(self.penasihat),
            "share_pengawas": float(self.pengawas),
            "share_dana_sosial": float(self.dana_sosial),
            "share_pades": float(self.pades),
            "share_modal_bumdes": float(self.modal_bumdes),
            "share_unit_pengelola": float(self.unit_pengelola),
            "share_unit_bumdes": float(self.unit_bumdes),
        }


async def get_bagi_hasil_config(session: AsyncSession) -> BagiHasilConfig:
    row = await get_org_profile(session)
    return BagiHasilConfig(
        pengurus=Decimal(row.share_pengurus),
        penasihat=Decimal(row.share_penasihat),
        pengawas=Decimal(row.share_pengawas),
        dana_sosial=Decimal(row.share_dana_sosial),
        pades=Decimal(row.share_pades),
        modal_bumdes=Decimal(row.share_modal_bumdes),
        unit_pengelola=Decimal(row.share_unit_pengelola),
        unit_bumdes=Decimal(row.share_unit_bumdes),
    )


def validate_bagi_hasil_fields(fields: dict) -> None:
    """Dipanggil dari router sebelum menyimpan patch OrgProfile. `fields`
    adalah dict hasil PATCH parsial -- validasi total 100% hanya dijalankan
    kalau salah satu field grup yang bersangkutan memang ikut diubah (nilai
    field lain di grup yang sama diasumsikan tetap konsisten dari sebelumnya,
    karena tiap penyimpanan sebelumnya sudah lolos validasi yang sama)."""
    if any(f in fields for f in SHARE_FIELDS_BUMDES):
        total = sum(Decimal(str(fields[f])) for f in SHARE_FIELDS_BUMDES if f in fields)
        # Field grup yang tidak ikut dikirim di PATCH ini tidak bisa divalidasi
        # totalnya dari sini -- router yang memanggil fungsi ini WAJIB mengisi
        # `fields` dengan nilai gabungan (existing + patch), bukan patch mentah.
        if abs(total - _HUNDRED) > _TOLERANCE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Total proporsi bagi hasil BUMDes (Pengurus + Penasihat + Pengawas + "
                    f"Dana Sosial + PADes + Penguatan Modal) harus 100%, saat ini {total}%"
                ),
            )
    if any(f in fields for f in SHARE_FIELDS_UNIT):
        total = sum(Decimal(str(fields[f])) for f in SHARE_FIELDS_UNIT if f in fields)
        if abs(total - _HUNDRED) > _TOLERANCE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Total proporsi bagi hasil Unit Usaha (Pengelola + BUMDes) harus 100%, saat ini {total}%",
            )


# ---------------------------------------------------------------------------
# Proporsi bagi hasil per unit usaha, berlaku per bulan (riwayat effective_from).
# Default (unit tanpa riwayat / bulan sebelum riwayat pertama) = pasangan
# share_unit_* di Profil BUMDes ("default untuk unit baru").


@dataclass(frozen=True)
class UnitShare:
    pengelola: Decimal
    bumdes: Decimal
    source: str = "default"  # default | riwayat | transfer


def month_start(period: str) -> date:
    return date(int(period[:4]), int(period[5:7]), 1)


def months_between(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


async def default_unit_share(session: AsyncSession) -> UnitShare:
    row = await get_org_profile(session)
    return UnitShare(Decimal(row.share_unit_pengelola), Decimal(row.share_unit_bumdes))


async def unit_share_history(session: AsyncSession, unit_id: str) -> list[UnitBagiHasilShare]:
    return list((await session.execute(
        select(UnitBagiHasilShare).where(UnitBagiHasilShare.unit_usaha_id == unit_id)
        .order_by(UnitBagiHasilShare.effective_from))).scalars())


def _pick(history: list[UnitBagiHasilShare], period: str, default: UnitShare) -> UnitShare:
    first = month_start(period)
    hit = None
    for h in history:
        if h.effective_from <= first:
            hit = h
    return UnitShare(Decimal(hit.share_pengelola), Decimal(hit.share_bumdes), "riwayat") if hit else default


async def effective_unit_share(session: AsyncSession, unit_id: str, period: str) -> UnitShare:
    """Proporsi yang berlaku untuk unit pada bulan `period` (YYYY-MM) menurut riwayat."""
    return _pick(await unit_share_history(session, unit_id), period, await default_unit_share(session))


async def unit_shares_by_month(session: AsyncSession, unit_id: str, periods: list[str]) -> dict[str, UnitShare]:
    """Proporsi per bulan untuk laporan: snapshot transfer (yang sungguh dipakai) bila ada, selain itu riwayat."""
    history = await unit_share_history(session, unit_id)
    default = await default_unit_share(session)
    snaps = {r.period: r for r in (await session.execute(
        select(BagiHasilTransferShare).where(BagiHasilTransferShare.unit_usaha_id == unit_id,
                                             BagiHasilTransferShare.period.in_(periods)))).scalars()} if periods else {}
    out = {}
    for p in periods:
        snap = snaps.get(p)
        out[p] = UnitShare(Decimal(snap.share_pengelola), Decimal(snap.share_bumdes), "transfer") if snap \
            else _pick(history, p, default)
    return out
