"""Belanja Modal Awal: pemasok opsional pada inventory_purchases.

Revision ID: 20261014_23_purchase_vendor_optional
Revises: 20261014_22_stock_category_description
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_23_purchase_vendor_optional"
down_revision = "20261014_22_stock_category_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("inventory_purchases", "vendor_id", existing_type=sa.String(36), nullable=True)


def downgrade() -> None:
    # Tidak memaksa NOT NULL lagi: baris Belanja Modal Awal boleh tanpa pemasok.
    pass
