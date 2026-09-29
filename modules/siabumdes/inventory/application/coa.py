"""COA account-code validation for UU05 inventory postings."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.infrastructure.models import Account


async def validate_coa_codes(session: AsyncSession, *codes: str) -> None:
    """Ensure each COA code exists and is active in siabumdes accounts."""
    missing: list[str] = []
    for raw in codes:
        code = (raw or "").strip()
        if not code:
            raise ValueError("kode akun COA wajib diisi")
        acc = await session.scalar(
            select(Account).where(Account.code == code, Account.active.is_(True))
        )
        if not acc:
            missing.append(code)
    if missing:
        raise ValueError(f"kode akun COA tidak ditemukan/nonaktif: {', '.join(missing)}")
