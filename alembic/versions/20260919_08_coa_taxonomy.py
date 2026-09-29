"""Lookup tables for account category / subcategory taxonomy.

Revision ID: 20260919_08_coa_taxonomy
Revises: 20260918_07_widen_varchar_limits
"""
from alembic import op

revision = "20260919_08_coa_taxonomy"
down_revision = "20260918_07_widen_varchar_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_categories (
            slug VARCHAR(64) PRIMARY KEY,
            label VARCHAR(128) NOT NULL,
            normal_balance VARCHAR(8) NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_subcategories (
            slug VARCHAR(128) PRIMARY KEY,
            category_slug VARCHAR(64) NOT NULL REFERENCES account_categories(slug),
            label VARCHAR(128) NOT NULL,
            is_system BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_subcategories_category ON account_subcategories (category_slug)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS account_subcategories")
    op.execute("DROP TABLE IF EXISTS account_categories")
