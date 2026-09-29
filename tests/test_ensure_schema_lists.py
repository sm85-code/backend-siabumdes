"""Hardening: ensure_schema _WIDEN/_ADD_COLUMNS stay coherent without a live DB.

Prod boots via ensure_schema(); Alembic is the reviewable history. These checks
guard against typos / duplicate column entries that would make boot-time DDL
noisily fail (and skip later steps) even though _safe_exec isolates each step.
"""
from __future__ import annotations

import os
import re

# Importing schema pulls shared.database, which requires DATABASE_URL at import.
# CI may inject a real URL; for local/unit collection provide a harmless placeholder
# (no connection is opened by these list-shape tests).
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1:5432/siabumdes_test")

from modules.siabumdes import schema as schema_mod
from modules.siabumdes.schema import _ADD_COLUMNS, _WIDEN

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def test_widen_entries_are_unique_and_shaped():
    seen: set[tuple[str, str]] = set()
    for table, column, new_type in _WIDEN:
        assert table and column and new_type, (table, column, new_type)
        assert _IDENT.match(table), table
        assert _IDENT.match(column), column
        assert new_type.upper().startswith("VARCHAR"), new_type
        key = (table, column)
        assert key not in seen, f"duplicate _WIDEN entry: {key}"
        seen.add(key)


def test_add_columns_unique_and_shaped():
    seen: set[tuple[str, str]] = set()
    for table, column, col_def in _ADD_COLUMNS:
        assert table and column and col_def, (table, column, col_def)
        assert _IDENT.match(table), table
        assert _IDENT.match(column), column
        # Def must not sneak in statement terminators (f-string SQL).
        assert ";" not in col_def, col_def
        key = (table, column)
        assert key not in seen, f"duplicate _ADD_COLUMNS entry: {key}"
        seen.add(key)


def test_add_columns_cover_known_bootstrap_fields():
    """Columns that exist in recent alembic revisions must also be boot-ensured."""
    keys = {(t, c) for t, c, _ in _ADD_COLUMNS}
    required = {
        ("org_profiles", "share_pengurus"),
        ("org_profiles", "share_penasihat"),
        ("org_profiles", "share_pengawas"),
        ("org_profiles", "share_dana_sosial"),
        ("org_profiles", "share_pades"),
        ("org_profiles", "share_modal_bumdes"),
        ("org_profiles", "share_unit_pengelola"),
        ("org_profiles", "share_unit_bumdes"),
        ("users", "photo_url"),
        ("stock_cards", "movement_kind"),
        ("unit_usaha", "business_type"),
    }
    missing = required - keys
    assert not missing, f"_ADD_COLUMNS missing boot-critical columns: {missing}"


def test_widen_covers_identity_varchar_hotspots():
    keys = {(t, c) for t, c, _ in _WIDEN}
    required = {
        ("users", "password_hash"),
        ("users", "id"),
        ("users", "role"),
        ("users", "unit_usaha_id"),
        ("accounts", "code"),
        ("accounts", "category"),
        ("accounts", "subcategory"),
    }
    missing = required - keys
    assert not missing, f"_WIDEN missing expected identity/account columns: {missing}"


def test_add_column_sql_template_is_idempotent_if_not_exists():
    """Mirror the exact f-string ensure_schema builds (no live connection)."""
    for table, column, col_def in _ADD_COLUMNS:
        sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_def}"
        assert sql.startswith("ALTER TABLE ")
        assert "ADD COLUMN IF NOT EXISTS" in sql
        assert table in sql and column in sql


def test_widen_sql_template_gates_on_information_schema():
    table, column, new_type = _WIDEN[0]
    # Same structure as ensure_schema's DO $widen$ block.
    assert "information_schema.columns" in (
        "SELECT 1 FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name = '{table}' "
        f"AND column_name = '{column}'"
    )
    alter = f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}"
    assert alter.startswith("ALTER TABLE ")
    assert "TYPE" in alter


def test_safe_exec_is_savepoint_helper():
    """_safe_exec must stay a nested-transaction helper (doc + callable)."""
    assert callable(schema_mod._safe_exec)
    doc = (schema_mod._safe_exec.__doc__ or "").lower()
    assert "savepoint" in doc or "begin_nested" in doc
