"""Nota multi-baris Stock In / Stock Out: stock_cards.nota_id.

Revision ID: 20261014_24_stock_card_nota
Revises: 20261014_23_purchase_vendor_optional
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_24_stock_card_nota"
down_revision = "20261014_23_purchase_vendor_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE stock_cards ADD COLUMN IF NOT EXISTS nota_id VARCHAR(36)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_stock_cards_nota_id ON stock_cards (nota_id)")


def downgrade() -> None:
    op.drop_index("ix_stock_cards_nota_id", table_name="stock_cards")
    op.drop_column("stock_cards", "nota_id")
