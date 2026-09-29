"""Widen varchar columns that truncate bcrypt hashes and UUID strings.

Revision ID: 20260918_07_widen_varchar_limits
Revises: 20260918_06_master_and_proofs
"""
from alembic import op

revision = "20260918_07_widen_varchar_limits"
down_revision = "20260918_06_master_and_proofs"
branch_labels = None
depends_on = None


# (table, column, new_type)
_ALTERS = [
    ("users", "id", "VARCHAR(64)"),
    ("users", "password_hash", "VARCHAR(255)"),
    ("users", "role", "VARCHAR(64)"),
    ("users", "unit_usaha_id", "VARCHAR(64)"),
    ("system_controls", "id", "VARCHAR(64)"),
    ("system_controls", "locked_by", "VARCHAR(64)"),
    ("closed_periods", "id", "VARCHAR(64)"),
    ("closed_periods", "group_code", "VARCHAR(32)"),
    ("closed_periods", "closed_by", "VARCHAR(64)"),
    ("unit_usaha", "id", "VARCHAR(64)"),
    ("accounts", "id", "VARCHAR(64)"),
    ("accounts", "code", "VARCHAR(64)"),
    ("accounts", "category", "VARCHAR(64)"),
    ("accounts", "subcategory", "VARCHAR(128)"),
    ("accounts", "parent_code", "VARCHAR(64)"),
    ("accounts", "unit_usaha_id", "VARCHAR(64)"),
    ("transaction_types", "id", "VARCHAR(64)"),
    ("transaction_types", "debit", "VARCHAR(64)"),
    ("transaction_types", "credit", "VARCHAR(64)"),
    ("mitra", "id", "VARCHAR(64)"),
    ("mitra", "unit_usaha_id", "VARCHAR(64)"),
    ("transactions", "id", "VARCHAR(64)"),
    ("transactions", "unit_usaha_id", "VARCHAR(64)"),
    ("transactions", "debit_account_code", "VARCHAR(64)"),
    ("transactions", "credit_account_code", "VARCHAR(64)"),
    ("transactions", "mitra_id", "VARCHAR(64)"),
    ("transactions", "created_by", "VARCHAR(64)"),
    ("journal_entries", "id", "VARCHAR(64)"),
    ("journal_entries", "transaction_id", "VARCHAR(64)"),
    ("journal_items", "id", "VARCHAR(64)"),
    ("journal_items", "journal_entry_id", "VARCHAR(64)"),
    ("journal_items", "account_id", "VARCHAR(64)"),
]


def upgrade() -> None:
    for table, column, new_type in _ALTERS:
        op.execute(
            f"""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = '{table}'
                      AND column_name = '{column}'
                ) THEN
                    EXECUTE 'ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}';
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    # Widening is safe; narrowing would truncate production data. No-op.
    pass
