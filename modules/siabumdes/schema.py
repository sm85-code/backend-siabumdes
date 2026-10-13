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
from modules.siabumdes.toko.infrastructure import models as _toko_models  # noqa: F401

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
    ("users", "riska_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("yield_payments", "transaction_id", "VARCHAR(64) REFERENCES transactions(id) ON DELETE RESTRICT"),
    ("yield_payments", "transaction_date", "DATE"),
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
    ("toko_entries", "transaction_id", "VARCHAR(64) REFERENCES transactions(id) ON DELETE RESTRICT"),
]

# DDL lanjutan setelah _ADD_COLUMNS (idempoten, tanpa menghapus data).
# Rollback input mingguan Manajemen Setoran (#37): kembalikan unik satu pembayaran
# per mitra per bulan. Kolom yield_payments.week (DEFAULT 0) dibiarkan, tidak dipakai.
_POST_DDL = [
    ("DROP INDEX IF EXISTS uq_yield_payment_period_week", "drop index uq_yield_payment_period_week"),
    ("CREATE UNIQUE INDEX IF NOT EXISTS uq_yield_payment_period ON yield_payments (partner_id, year, month)",
     "restore unique uq_yield_payment_period"),
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
        # Riska central drafts/dialogue have no unit FK. Existing deployments do not run Alembic on boot.
        await _safe_exec(conn, "ALTER TABLE riska_drafts ALTER COLUMN unit_usaha_id DROP NOT NULL",
                         "allow central Riska drafts")
        from modules.siabumdes.application.riska_migration import LEGACY_ASSIGNMENTS, CONSUME_LEGACY_GRANTS
        # The two statements must commit together; never restore a finished session at restart.
        async with conn.begin_nested():
            await conn.execute(text(LEGACY_ASSIGNMENTS))
            await conn.execute(text(CONSUME_LEGACY_GRANTS))
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
        for sql, label in _POST_DDL:
            await _safe_exec(conn, sql, label)
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
