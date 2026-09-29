"""Create missing tables and widen short varchar columns on boot.

App Platform has no one-off alembic job. Neon starts empty, so seed
used to hit relation users does not exist.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from shared.database import Base, engine

from modules.siabumdes.identity.infrastructure import models as _identity_models  # noqa: F401
from modules.siabumdes.infrastructure import models as _siabumdes_models  # noqa: F401
from modules.siabumdes.inventory.infrastructure import models as _inventory_models  # noqa: F401

logger = logging.getLogger("sm85.schema")

_WIDEN = [
    ("users", "password_hash", "VARCHAR(255)"),
    ("users", "id", "VARCHAR(64)"),
    ("users", "role", "VARCHAR(64)"),
    ("users", "unit_usaha_id", "VARCHAR(64)"),
    ("accounts", "code", "VARCHAR(64)"),
    ("accounts", "category", "VARCHAR(64)"),
    ("accounts", "subcategory", "VARCHAR(128)"),
]

# Kolom baru pada tabel yang SUDAH ADA -- create_all() di atas cuma bikin tabel
# yang belum ada, tidak menambah kolom ke tabel existing. Proporsi bagi hasil
# (dulu hardcode di closing.py/reporting.py, sekarang diedit lewat menu Profil
# BUMDES) butuh ADD COLUMN eksplisit di sini.
_ADD_COLUMNS = [
    ("org_profiles", "share_pengurus", "NUMERIC(5,2) NOT NULL DEFAULT 35"),
    ("org_profiles", "share_penasihat", "NUMERIC(5,2) NOT NULL DEFAULT 7"),
    ("org_profiles", "share_pengawas", "NUMERIC(5,2) NOT NULL DEFAULT 5"),
    ("org_profiles", "share_dana_sosial", "NUMERIC(5,2) NOT NULL DEFAULT 5"),
    ("org_profiles", "share_pades", "NUMERIC(5,2) NOT NULL DEFAULT 30"),
    ("org_profiles", "share_modal_bumdes", "NUMERIC(5,2) NOT NULL DEFAULT 18"),
    ("org_profiles", "share_unit_pengelola", "NUMERIC(5,2) NOT NULL DEFAULT 30"),
    ("org_profiles", "share_unit_bumdes", "NUMERIC(5,2) NOT NULL DEFAULT 70"),
    ("users", "photo_url", "VARCHAR(500) NOT NULL DEFAULT ''"),
    ("stock_cards", "movement_kind", "VARCHAR(16) NOT NULL DEFAULT 'sale'"),
    ("unit_usaha", "business_type", "VARCHAR(20) NOT NULL DEFAULT 'jasa'"),
]


async def _safe_exec(conn, sql: str, label: str) -> bool:
    """Run one DDL/DML statement in its own SAVEPOINT.

    _WIDEN/_ADD_COLUMNS run inside one big transaction; without a savepoint
    per statement, a single failing entry (e.g. a stale/incompatible one)
    aborts the whole transaction and silently skips every statement after
    it -- including ones for brand-new columns further down the list, since
    main.py's lifespan swallows the resulting exception to keep booting.
    Returns True on success so callers can gate follow-up steps on it.
    """
    try:
        async with conn.begin_nested():
            await conn.execute(text(sql))
        return True
    except Exception:
        logger.exception("schema step failed: %s", label)
        return False


async def ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, column, new_type in _WIDEN:
            await _safe_exec(
                conn,
                f"""
                DO $widen$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = '{table}'
                          AND column_name = '{column}'
                    ) THEN
                        EXECUTE 'ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}';
                    END IF;
                END
                $widen$;
                """,
                f"widen {table}.{column}",
            )
        try:
            business_type_existed = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'unit_usaha' "
                        "AND column_name = 'business_type'"
                    )
                )
            ).first() is not None
        except Exception:
            logger.exception("schema step failed: check unit_usaha.business_type existence")
            business_type_existed = True  # asumsikan sudah ada -- lebih aman skip backfill daripada menimpa data admin
        for table, column, col_def in _ADD_COLUMNS:
            await _safe_exec(
                conn,
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_def}",
                f"add column {table}.{column}",
            )
        if not business_type_existed:
            # unit_usaha.business_type baru ditambahkan dengan default 'jasa' di atas;
            # UU05/UU06 sudah dipakai sebagai unit dagang (HPP) sebelum kolom ini ada,
            # jadi tanpa backfill ini laporan laba-rugi mereka kehilangan baris HPP.
            # Hanya dijalankan sekali persis saat kolom ini baru dibuat (dicek di atas
            # SEBELUM ALTER TABLE), supaya perubahan admin lewat Profil Unit Usaha
            # sesudahnya tidak pernah ditimpa balik di boot berikutnya.
            await _safe_exec(
                conn,
                "UPDATE unit_usaha SET business_type = 'perdagangan' WHERE code IN ('UU05', 'UU06')",
                "backfill unit_usaha.business_type",
            )
    logger.info("schema ready")
