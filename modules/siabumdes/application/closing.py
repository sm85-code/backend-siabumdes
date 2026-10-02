"""Automated monthly closing journals (per-entity, slug-mapped)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.identity.infrastructure.models import ClosedPeriod
from modules.siabumdes.application.bagi_hasil import get_bagi_hasil_config
from modules.siabumdes.money_json import money_str
from modules.siabumdes.application.reporting import ReportingService
from modules.siabumdes.application.services import FinanceService
from modules.siabumdes.infrastructure.models import (
    Account,
    JournalEntry,
    JournalItem,
    Transaction,
    UnitUsaha,
)
from modules.siabumdes.coa_taxonomy import (
    SUB_IKHTISAR_LR,
    SUB_SALDO_LABA,
)
from modules.siabumdes.period import (
    assert_period_kind_matches_group,
    period_kind,
    period_range as _period_range,
)

CENT = Decimal("0.01")

SUB_UTANG_BH_BUMDES = "utang_bagi_hasil_bumdes"
SUB_UTANG_BH_UNIT = "utang_bagi_hasil_unit"

# Rasio alokasi laba BUMDES (dulu konstanta BUMDES_ALLOC hardcode di sini) kini
# dibaca dari OrgProfile lewat bagi_hasil.get_bagi_hasil_config() -- lihat
# BagiHasilConfig.bumdes_alloc(), dipanggil di run_monthly_close() di bawah.


def _money(value: float | Decimal | int) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _close_ref(period: str, group: str) -> str:
    return f"CLOSE-{period}-{group}"


async def _account_by_slug(session: AsyncSession, group: str, slug: str) -> Account:
    row = (
        await session.execute(
            select(Account).where(
                Account.group_code == group,
                Account.subcategory == slug,
                Account.active.is_(True),
            )
        )
    ).scalars().first()
    if not row:
        raise ValueError(
            f"Akun slug '{slug}' tidak ditemukan di grup {group}. Periksa COA."
        )
    return row


async def _resolve_unit(session: AsyncSession, group: str) -> Optional[UnitUsaha]:
    if group == "BUMDES":
        return None
    unit = (
        await session.execute(select(UnitUsaha).where(UnitUsaha.code == group))
    ).scalar_one_or_none()
    if not unit:
        raise ValueError(f"Grup {group} tidak ditemukan")
    return unit


async def _post_pair(
    session: AsyncSession,
    *,
    end: date,
    unit_id: Optional[str],
    debit: Account,
    credit: Account,
    amount: Decimal,
    desc: str,
    reference: str,
    actor_id: str,
) -> Transaction:
    if amount <= 0:
        raise ValueError("amount jurnal penutup harus > 0")
    tx = Transaction(
        date=end,
        unit_usaha_id=unit_id,
        transaction_type="jurnal_penutup",
        description=desc,
        amount=amount,
        debit_account_code=debit.code,
        credit_account_code=credit.code,
        reference=reference,
        created_by=actor_id,
        created_at=datetime.now(timezone.utc),
        is_closing=True,
        proofs=[],
    )
    session.add(tx)
    await session.flush()
    finance = FinanceService(session)
    await finance.create_journal_entry(
        transaction_id=tx.id,
        entry_date=end,
        memo=desc,
        debit_account_id=debit.id,
        credit_account_id=credit.id,
        amount=amount,
    )
    return tx


async def run_monthly_close(
    session: AsyncSession,
    *,
    period: str,
    group: str,
    actor_id: str,
) -> dict[str, Any]:
    """Both BUMDES (Pusat) and unit usaha (UU01..UU06) close monthly
    ("YYYY-MM") -- see assert_period_kind_matches_group."""
    group_code = (group or "BUMDES").strip().upper()
    if group_code != "BUMDES":
        unit_exists = await session.scalar(select(UnitUsaha.id).where(UnitUsaha.code == group_code))
        if not unit_exists:
            raise ValueError(f"Grup {group_code} tidak valid")
    assert_period_kind_matches_group(period or "", group_code)

    existing = (
        await session.execute(
            select(ClosedPeriod).where(
                ClosedPeriod.period == period,
                ClosedPeriod.group_code == group_code,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise ValueError(f"Periode {period} ({group_code}) sudah ditutup")

    start, end = _period_range(period)
    unit = await _resolve_unit(session, group_code)
    unit_id = unit.id if unit else None
    reference = _close_ref(period, group_code)

    ikhtisar = await _account_by_slug(session, group_code, SUB_IKHTISAR_LR)
    reports = ReportingService(session)
    lr = await reports.laba_rugi(start, end, unit_id, entity=group_code)
    laba = _money(lr.get("laba_bersih") or 0)
    accounts = {
        acc.code: acc
        for acc in (
            await session.execute(select(Account).where(Account.group_code == group_code))
        ).scalars()
    }

    created = 0
    by_plain = {code.split("-", 1)[-1]: acc for code, acc in accounts.items()}

    async def _close_row(code: str, amount: float, *, income: bool) -> None:
        nonlocal created
        amt = _money(amount)
        raw = (code or "").strip()
        acc = accounts.get(raw) or by_plain.get(raw) or by_plain.get(raw.split("-", 1)[-1])
        if not acc or amt == 0:
            return
        # `amount`'s sign follows the account's normal side (positive = a
        # pendapatan account sitting in credit, or a beban/HPP account
        # sitting in debit, as expected). A reversed balance for the
        # period (e.g. a correcting entry that nets a pendapatan account
        # into debit, or a beban account into credit) must still be
        # closed to zero -- just via the opposite pair -- so it doesn't
        # carry a leftover balance into the next period while laba_bersih
        # (computed independently above) already accounted for it.
        reversed_balance = amt < 0
        acc_is_debit = income != reversed_balance
        debit, credit = (acc, ikhtisar) if acc_is_debit else (ikhtisar, acc)
        label = "pendapatan" if income else "beban/HPP"
        await _post_pair(
            session,
            end=end,
            unit_id=unit_id,
            debit=debit,
            credit=credit,
            amount=abs(amt),
            desc=f"Tutup {label} {acc.code} {period}",
            reference=reference,
            actor_id=actor_id,
        )
        created += 1

    for row in lr.get("pendapatan") or []:
        await _close_row(row["code"], row["amount"], income=True)
    for row in (lr.get("hpp") or []) + (lr.get("beban") or []):
        await _close_row(row["code"], row["amount"], income=False)

    if laba > 0:
        if group_code == "BUMDES":
            bumdes_alloc = (await get_bagi_hasil_config(session)).bumdes_alloc()
            remaining = laba
            last_idx = len(bumdes_alloc) - 1
            for i, (slug, ratio) in enumerate(bumdes_alloc):
                dest = await _account_by_slug(session, group_code, slug)
                portion = remaining if i == last_idx else _money(laba * ratio)
                remaining -= portion
                if portion <= 0:
                    continue
                await _post_pair(
                    session,
                    end=end,
                    unit_id=unit_id,
                    debit=ikhtisar,
                    credit=dest,
                    amount=portion,
                    desc=f"Alokasi laba {period} ke {slug}",
                    reference=reference,
                    actor_id=actor_id,
                )
                created += 1
        else:
            dest = await _account_by_slug(session, group_code, SUB_UTANG_BH_UNIT)
            await _post_pair(
                session,
                end=end,
                unit_id=unit_id,
                debit=ikhtisar,
                credit=dest,
                amount=laba,
                desc=f"Alokasi laba unit {period} ke utang bagi hasil",
                reference=reference,
                actor_id=actor_id,
            )
            created += 1
        outcome = "laba"
    elif laba < 0:
        saldo = await _account_by_slug(session, group_code, SUB_SALDO_LABA)
        await _post_pair(
            session,
            end=end,
            unit_id=unit_id,
            debit=saldo,
            credit=ikhtisar,
            amount=abs(laba),
            desc=f"Transfer rugi {period} ke saldo laba/rugi",
            reference=reference,
            actor_id=actor_id,
        )
        created += 1
        outcome = "rugi"
    else:
        outcome = "impas"

    row = ClosedPeriod(
        period=period,
        group_code=group_code,
        laba_bersih=laba,
        entries=created,
        closed_by=actor_id,
    )
    session.add(row)
    await session.flush()
    return {
        "closed": True,
        "period": period,
        "group": group_code,
        "entries": created,
        "laba_bersih": money_str(laba),
        "outcome": outcome,
    }


async def undo_monthly_close(session: AsyncSession, period: str, group: str) -> int:
    period_kind(period or "")
    group_code = (group or "BUMDES").strip().upper()
    row = (
        await session.execute(
            select(ClosedPeriod).where(
                ClosedPeriod.period == period,
                ClosedPeriod.group_code == group_code,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise LookupError("Periode tertutup tidak ditemukan")

    # Impor lokal: bagi_hasil_transfer mengimpor modul ini (hindari siklus).
    from modules.siabumdes.application.bagi_hasil_transfer import later_or_equal_transfer_periods

    blocking = await later_or_equal_transfer_periods(session, period, group_code)
    if blocking:
        raise ValueError(
            f"Batalkan transfer bagi hasil periode {', '.join(blocking)} ({group_code}) terlebih dahulu"
        )

    reference = _close_ref(period, group_code)
    txs = list(
        (
            await session.execute(
                select(Transaction).where(
                    Transaction.reference == reference,
                    Transaction.is_closing.is_(True),
                )
            )
        ).scalars()
    )
    tx_ids = [tx.id for tx in txs]
    if tx_ids:
        entry_ids = list(
            (
                await session.execute(
                    select(JournalEntry.id).where(JournalEntry.transaction_id.in_(tx_ids))
                )
            ).scalars()
        )
        if entry_ids:
            await session.execute(delete(JournalItem).where(JournalItem.journal_entry_id.in_(entry_ids)))
            await session.execute(delete(JournalEntry).where(JournalEntry.id.in_(entry_ids)))
        await session.execute(delete(Transaction).where(Transaction.id.in_(tx_ids)))

    deleted = len(tx_ids)
    await session.delete(row)
    await session.flush()
    return deleted
