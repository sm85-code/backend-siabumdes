"""COA / transaction-type template helpers for dynamic units.

Live COA lives in DB (Kode Akun menu). data/coa_code.xlsx is the initial
seed template only. New detachable units should not start empty: clone
accounts + transaction types from an existing same-business_type unit.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.infrastructure.models import Account, TransactionType, UnitUsaha

# Preferred seed codes when several same-type units exist (stable defaults).
_PREFERRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "jasa": ("UU01", "UU02", "UU03", "UU04"),
    "perdagangan": ("UU05", "UU06"),
    "manufaktur": ("UU05", "UU06"),
}


async def coa_group_codes(session: AsyncSession) -> set[str]:
    """Valid Excel sheet / group codes: BUMDES + every unit code in DB.

    Includes inactive units so admins can still import/repair COA for a
    soft-deactivated unit. New transactions remain blocked separately via
    assert_unit_active.
    """
    codes = {row for row in (await session.execute(select(UnitUsaha.code))).scalars()}
    codes.add("BUMDES")
    return codes


async def find_coa_template_unit(
    session: AsyncSession,
    business_type: str,
    *,
    exclude_unit_id: Optional[str] = None,
    clone_from_code: Optional[str] = None,
) -> Optional[UnitUsaha]:
    """Pick a source unit whose COA/tx-types will be cloned.

    Prefer an explicit clone_from code when given; otherwise the first
    active same-business_type unit that already has accounts, with a
    stable preference order for seed units.
    """
    if clone_from_code:
        code = clone_from_code.strip().upper()
        unit = (
            await session.execute(select(UnitUsaha).where(UnitUsaha.code == code))
        ).scalar_one_or_none()
        if not unit:
            return None
        if exclude_unit_id and unit.id == exclude_unit_id:
            return None
        return unit

    stmt = (
        select(UnitUsaha)
        .where(UnitUsaha.business_type == business_type, UnitUsaha.active.is_(True))
        .order_by(UnitUsaha.code.asc())
    )
    if exclude_unit_id:
        stmt = stmt.where(UnitUsaha.id != exclude_unit_id)
    candidates = list((await session.execute(stmt)).scalars())
    if not candidates:
        return None

    preferred = _PREFERRED_BY_TYPE.get(business_type, ())
    ordered: list[UnitUsaha] = []
    by_code = {u.code: u for u in candidates}
    for code in preferred:
        if code in by_code:
            ordered.append(by_code.pop(code))
    ordered.extend(sorted(by_code.values(), key=lambda u: u.code))

    for unit in ordered:
        count = await session.scalar(
            select(func.count()).select_from(Account).where(Account.group_code == unit.code)
        )
        if count and count > 0:
            return unit
    return None


async def clone_coa_and_tx_types(
    session: AsyncSession,
    *,
    source: UnitUsaha,
    target: UnitUsaha,
) -> dict:
    """Copy Account + TransactionType rows from source.group to target.group.

    Idempotent: skips codes that already exist on the target group.
    Does not copy transactions, inventory, or mitra. Debit/credit account
    codes on transaction types are left as-is (same chart numbering across
    units of the same type).
    """
    src_group = source.code
    dst_group = target.code
    accounts_cloned = 0
    types_cloned = 0

    src_accounts = list(
        (await session.execute(select(Account).where(Account.group_code == src_group))).scalars()
    )
    existing_acc = set(
        (await session.execute(select(Account.code).where(Account.group_code == dst_group))).scalars()
    )
    for acc in src_accounts:
        if acc.code in existing_acc:
            continue
        session.add(
            Account(
                code=acc.code,
                name=acc.name,
                category=acc.category,
                subcategory=acc.subcategory,
                normal_balance=acc.normal_balance,
                parent_code=acc.parent_code,
                group_code=dst_group,
                unit_usaha_id=target.id,
                active=acc.active,
            )
        )
        accounts_cloned += 1

    src_types = list(
        (
            await session.execute(select(TransactionType).where(TransactionType.group_code == src_group))
        ).scalars()
    )
    existing_tt = set(
        (
            await session.execute(
                select(TransactionType.code).where(TransactionType.group_code == dst_group)
            )
        ).scalars()
    )
    for tt in src_types:
        if tt.code in existing_tt:
            continue
        unit_codes = list(tt.unit_codes or [])
        # Remap source unit code references to the new unit when present.
        remapped = [dst_group if c == src_group else c for c in unit_codes]
        if dst_group not in remapped and src_group in (tt.unit_codes or []):
            remapped.append(dst_group)
        session.add(
            TransactionType(
                code=tt.code,
                name=tt.name,
                debit=tt.debit,
                credit=tt.credit,
                group_code=dst_group,
                unit_codes=remapped,
            )
        )
        types_cloned += 1

    if accounts_cloned or types_cloned:
        await session.flush()
    return {
        "source_unit": src_group,
        "accounts_cloned": accounts_cloned,
        "transaction_types_cloned": types_cloned,
    }
