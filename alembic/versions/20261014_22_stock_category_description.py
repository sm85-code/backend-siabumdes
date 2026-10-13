"""Keterangan (description) pada kategori produk.

Revision ID: 20261014_22_stock_category_description
Revises: 20261014_21_yield_payment_week
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_22_stock_category_description"
down_revision = "20261014_21_yield_payment_week"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("stock_categories")}
    if "description" not in cols:
        op.add_column("stock_categories", sa.Column("description", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("stock_categories", "description")
