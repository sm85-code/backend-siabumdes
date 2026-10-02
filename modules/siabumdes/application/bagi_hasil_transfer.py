"""Transfer (pembayaran) bagi hasil: melunasi saldo utang bagi hasil setelah tutup buku.

BUMDES (Pusat): per triwulan (Mar/Jun/Sep/Des). Saldo `utang_bagi_hasil_bumdes`
-- akumulasi 3 bulan -- dibayarkan ke Pengurus/Penasihat/Pengawas/Dana Sosial
dengan proporsi 35:7:5:5 (dari konfigurasi Profil BUMDES), kas dikurangi.

Unit usaha (UU01..UU06): per bulan. Saldo `utang_bagi_hasil_unit` dibayarkan
ke BUMDES (70%) dan Pengelola (30%). Karena pencatatan tiap kelompok terpisah
(tidak ada konsolidasi), bagian BUMDES juga dicatat di Pusat sebagai kas masuk
dan Pendapatan Bagi Hasil, bertanggal AKHIR bulan terpilih (masuk laba Pusat
bulan itu -- Pusat tutup buku setelah semua unit transfer).

Transaksi pembayaran bertanggal tanggal 1 bulan berikutnya; semua transaksi memakai referensi
`BAGIHASIL-{periode}-{kelompok}` sebagai penanda (cegah ganda, dasar pembatalan).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.application.bagi_hasil import BagiHasilConfig, get_bagi_hasil_config
from modules.siabumdes.application.closing import (
    SUB_UTANG_BH_BUMDES,
    SUB_UTANG_BH_UNIT,
)
from modules.siabumdes.application.services import FinanceService
from modules.siabumdes.coa_taxonomy import SUB_KAS_BANK
from modules.siabumdes.identity.infrastructure.models import ClosedPeriod
from modules.siabumdes.infrastructure.models import (
    Account,
    JournalEntry,
    JournalItem,
    Transaction,
    UnitUsaha,
)
from modules.siabumdes.money_json import money_str
from modules.siabumdes.period import period_kind, period_range

BH_REF_PREFIX = "BAGIHASIL-"
# Bulan yang boleh ditransfer untuk kelompok BUMDES (akhir triwulan).
BUMDES_TRANSFER_MONTHS = (3, 6, 9, 12)

# Kode akun tetap di sisi Pusat (sesuai COA BUMDES).
KAS_PUSAT_CODE = "1.1.01.01"
PENDAPATAN_BH_PUSAT_CODE = "4.1.01.02"
TYPE_PUSAT_MASUK = "bagi_hasil_unit_masuk"

CENT = Decimal("0.01")


def bh_ref(period: str, group: str) -> str:
    return f"{BH_REF_PREFIX}{period}-{group}"


def is_bh_reference(reference: Optional[str]) -> bool:
    return (reference or "").startswith(BH_REF_PREFIX)


def next_month_first(period: str) -> date:
    _, end = period_range(period)
    return date.fromordinal(end.toordinal() + 1)


def _money(value: Decimal | int | float) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def split_amount(total: Decimal, shares: list[tuple[str, Decimal]]) -> list[tuple[str, Decimal]]:
    """Bagi `total` proporsional terhadap `shares` (persen apa pun); selisih
    pembulatan masuk ke elemen terakhir yang bernilai > 0 sehingga jumlahnya
    selalu persis `total`. Bagian bernilai 0 dibuang."""
    weight = sum((pct for _, pct in shares), Decimal("0"))
    if weight <= 0:
        raise ValueError("Total proporsi bagi hasil harus > 0")
    live = [(label, pct) for label, pct in shares if pct > 0]
    out: list[tuple[str, Decimal]] = []
    remaining = _money(total)
    for i, (label, pct) in enumerate(live):
        part = remaining if i == len(live) - 1 else _money(total * pct / weight)
        remaining -= part
        out.append((label, part))
    return [(label, amt) for label, amt in out if amt > 0]


def bumdes_shares(cfg: BagiHasilConfig) -> list[tuple[str, Decimal]]:
    return [
        ("Pengurus", cfg.pengurus),
        ("Penasihat", cfg.penasihat),
        ("Pengawas", cfg.pengawas),
        ("Dana Sosial", cfg.dana_sosial),
    ]


def unit_shares(cfg: BagiHasilConfig) -> list[tuple[str, Decimal]]:
    return [("BUMDES", cfg.unit_bumdes), ("Pengelola", cfg.unit_pengelola)]


def _slug(label: str) -> str:
    return label.lower().replace(" ", "_")


def _quarter_months(period: str) -> list[str]:
    year, month = int(period[:4]), int(period[5:7])
    return [f"{year}-{m:02d}" for m in range(month - 2, month + 1)]


async def _is_closed(session: AsyncSession, period: str, group: str) -> bool:
    return (
        await session.scalar(
            select(ClosedPeriod.id).where(ClosedPeriod.period == period, ClosedPeriod.group_code == group)
        )
    ) is not None


async def _account_by_slug(session: AsyncSession, group: str, slug: str) -> Account:
    row = (
        await session.execute(
            select(Account)
            .where(Account.group_code == group, Account.subcategory == slug, Account.active.is_(True))
            .order_by(Account.code)
        )
    ).scalars().first()
    if not row:
        raise ValueError(f"Akun slug '{slug}' tidak ditemukan di grup {group}. Periksa COA.")
    return row


async def _account_by_code(session: AsyncSession, group: str, code: str) -> Account:
    row = (
        await session.execute(
            select(Account).where(Account.group_code == group, Account.code == code, Account.active.is_(True))
        )
    ).scalar_one_or_none()
    if not row:
        raise ValueError(f"Akun {code} tidak ditemukan di grup {group}. Periksa COA.")
    return row


async def _kas_account(session: AsyncSession, group: str) -> Account:
    if group == "BUMDES":
        return await _account_by_code(session, group, KAS_PUSAT_CODE)
    return await _account_by_slug(session, group, SUB_KAS_BANK)


async def _saldo_utang(
    session: AsyncSession, utang: Account, unit_id: Optional[str], end: date
) -> Decimal:
    """Saldo normal-kredit akun utang sampai `end`: kredit - debit."""
    stmt = select(Transaction.debit_account_code, Transaction.credit_account_code, Transaction.amount).where(
        Transaction.date <= end,
        or_(Transaction.debit_account_code == utang.code, Transaction.credit_account_code == utang.code),
    )
    stmt = stmt.where(Transaction.unit_usaha_id.is_(None) if unit_id is None else Transaction.unit_usaha_id == unit_id)
    saldo = Decimal("0")
    for debit_code, credit_code, amount in (await session.execute(stmt)).all():
        amt = Decimal(str(amount))
        if credit_code == utang.code:
            saldo += amt
        if debit_code == utang.code:
            saldo -= amt
    return _money(saldo)


async def _post(
    session: AsyncSession,
    *,
    when: date,
    when_override: Optional[date] = None,
    unit_id: Optional[str],
    tx_type: str,
    desc: str,
    amount: Decimal,
    debit: Account,
    credit: Account,
    reference: str,
    actor_id: str,
) -> Transaction:
    when = when_override or when
    tx = Transaction(
        date=when,
        unit_usaha_id=unit_id,
        transaction_type=tx_type,
        description=desc,
        amount=amount,
        debit_account_code=debit.code,
        credit_account_code=credit.code,
        reference=reference,
        created_by=actor_id,
        proofs=[],
    )
    session.add(tx)
    await session.flush()
    await FinanceService(session).create_journal_entry(
        transaction_id=tx.id,
        entry_date=when,
        memo=desc,
        debit_account_id=debit.id,
        credit_account_id=credit.id,
        amount=amount,
    )
    return tx


async def run_bagi_hasil_transfer(
    session: AsyncSession, *, period: str, group: str, actor_id: str
) -> dict[str, Any]:
    group_code = (group or "BUMDES").strip().upper()
    period_kind(period or "")  # validasi format YYYY-MM
    month = int(period[5:7])

    unit: Optional[UnitUsaha] = None
    if group_code == "BUMDES":
        if month not in BUMDES_TRANSFER_MONTHS:
            raise ValueError("Transfer bagi hasil BUMDES hanya untuk periode Maret, Juni, September, Desember")
    else:
        unit = (await session.execute(select(UnitUsaha).where(UnitUsaha.code == group_code))).scalar_one_or_none()
        if not unit:
            raise ValueError(f"Grup {group_code} tidak valid")

    # Periode terpilih harus sudah tutup buku; BUMDES: seluruh bulan dalam triwulan.
    needed = _quarter_months(period)[:-1] + [period] if group_code == "BUMDES" else [period]
    for p in reversed(needed):
        if not await _is_closed(session, p, group_code):
            raise ValueError(f"Periode {p} ({group_code}) Belum Tutup Buku")

    ref = bh_ref(period, group_code)
    if await session.scalar(select(Transaction.id).where(Transaction.reference == ref).limit(1)):
        raise ValueError(f"Transfer bagi hasil {period} ({group_code}) sudah dilakukan")
    later = (
        await session.execute(
            select(Transaction.reference)
            .where(Transaction.reference.like(f"{BH_REF_PREFIX}%-{group_code}"))
            .distinct()
        )
    ).scalars().all()
    if any(r[len(BH_REF_PREFIX) : len(BH_REF_PREFIX) + 7] > period for r in later):
        raise ValueError(f"Transfer bagi hasil periode yang lebih baru sudah ada untuk {group_code}")

    cfg = await get_bagi_hasil_config(session)
    _, end = period_range(period)
    when = next_month_first(period)
    unit_id = unit.id if unit else None

    utang = await _account_by_slug(
        session, group_code, SUB_UTANG_BH_BUMDES if group_code == "BUMDES" else SUB_UTANG_BH_UNIT
    )
    kas = await _kas_account(session, group_code)
    saldo = await _saldo_utang(session, utang, unit_id, end)
    if saldo <= 0:
        raise ValueError(f"Saldo {utang.code} ({group_code}) sampai {end.isoformat()} nol, tidak ada yang ditransfer")

    parts = split_amount(saldo, bumdes_shares(cfg) if group_code == "BUMDES" else unit_shares(cfg))

    items: list[dict[str, str]] = []
    for label, amount in parts:
        await _post(
            session,
            when=when,
            unit_id=unit_id,
            tx_type=f"bagi_hasil_{_slug(label)}",
            desc=f"Pembayaran Bagi Hasil {label} {period}",
            amount=amount,
            debit=utang,
            credit=kas,
            reference=ref,
            actor_id=actor_id,
        )
        items.append({"label": label, "amount": money_str(amount)})
        if group_code != "BUMDES" and label == "BUMDES":
            # Pencatatan terpisah: sisi penerima di Pusat.
            await _post(
                session,
                when=when,
                unit_id=None,
                tx_type=TYPE_PUSAT_MASUK,
                desc=f"Penerimaan Bagi Hasil {group_code} {period}",
                when_override=end,
                amount=amount,
                debit=await _account_by_code(session, "BUMDES", KAS_PUSAT_CODE),
                credit=await _account_by_code(session, "BUMDES", PENDAPATAN_BH_PUSAT_CODE),
                reference=ref,
                actor_id=actor_id,
            )

    return {
        "transferred": True,
        "period": period,
        "group": group_code,
        "date": when.isoformat(),
        "pusat_date": end.isoformat(),
        "total": money_str(saldo),
        "items": items,
    }


async def transfer_transactions(session: AsyncSession, period: str, group: str) -> list[Transaction]:
    ref = bh_ref(period, (group or "BUMDES").strip().upper())
    return list(
        (await session.execute(select(Transaction).where(Transaction.reference == ref))).scalars()
    )


async def undo_bagi_hasil_transfer(session: AsyncSession, txs: list[Transaction]) -> int:
    tx_ids = [tx.id for tx in txs]
    if tx_ids:
        entry_ids = list(
            (await session.execute(select(JournalEntry.id).where(JournalEntry.transaction_id.in_(tx_ids)))).scalars()
        )
        if entry_ids:
            await session.execute(delete(JournalItem).where(JournalItem.journal_entry_id.in_(entry_ids)))
            await session.execute(delete(JournalEntry).where(JournalEntry.id.in_(entry_ids)))
        await session.execute(delete(Transaction).where(Transaction.id.in_(tx_ids)))
    await session.flush()
    return len(tx_ids)


async def list_bagi_hasil_transfers(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(
                Transaction.reference,
                func.min(Transaction.date),
                func.min(Transaction.created_at),
                func.count(Transaction.id),
                func.coalesce(func.sum(Transaction.amount).filter(Transaction.transaction_type != TYPE_PUSAT_MASUK), 0),
            )
            .where(Transaction.reference.like(f"{BH_REF_PREFIX}%"))
            .group_by(Transaction.reference)
        )
    ).all()
    out = []
    for ref, tx_date, created, count, total in rows:
        period, group = ref[len(BH_REF_PREFIX) : len(BH_REF_PREFIX) + 7], ref[len(BH_REF_PREFIX) + 8 :]
        out.append(
            {
                "period": period,
                "group": group,
                "date": tx_date.isoformat(),
                "total": money_str(total),
                "entries": count,
                "transferred_at": created.isoformat() if created else None,
            }
        )
    return sorted(out, key=lambda r: (r["period"], r["group"]), reverse=True)


async def later_or_equal_transfer_periods(session: AsyncSession, period: str, group: str) -> list[str]:
    """Periode transfer milik `group` yang >= `period` (dipakai untuk memblokir
    pembatalan tutup buku yang masih ditopang transfer)."""
    refs = (
        await session.execute(
            select(Transaction.reference)
            .where(Transaction.reference.like(f"{BH_REF_PREFIX}%-{group}"))
            .distinct()
        )
    ).scalars().all()
    periods = [r[len(BH_REF_PREFIX) : len(BH_REF_PREFIX) + 7] for r in refs]
    return sorted(p for p in periods if p >= period)


async def units_with_unpaid_bagi_hasil(session: AsyncSession, period: str) -> list[tuple[str, Decimal]]:
    """Unit yang masih punya saldo utang bagi hasil untuk `period` (belum ditransfer).

    Saldo dihitung sampai tanggal 1 bulan berikutnya, supaya pembayaran transfer
    (bertanggal 1 bulan berikutnya) ikut mengurangi; transfer periode yang lebih baru
    yang sudah melunasi saldo ini juga terhitung."""
    cutoff = next_month_first(period)
    out: list[tuple[str, Decimal]] = []
    for unit in (await session.execute(select(UnitUsaha).order_by(UnitUsaha.code))).scalars():
        utang = (
            await session.execute(
                select(Account).where(Account.group_code == unit.code, Account.subcategory == SUB_UTANG_BH_UNIT)
            )
        ).scalars().first()
        if not utang:
            continue
        saldo = await _saldo_utang(session, utang, unit.id, cutoff)
        if saldo > 0:
            out.append((unit.code, saldo))
    return out
