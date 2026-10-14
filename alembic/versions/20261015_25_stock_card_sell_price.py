"""stock_cards.sell_price: harga jual per baris Stock Out.

Revision ID: 20261015_25_stock_card_sell_price
Revises: 20261014_24_stock_card_nota

Catatan: di produksi kolom ini ditambahkan oleh ensure_schema() lewat _ADD_COLUMNS.
"""

from alembic import op

revision = "20261015_25_stock_card_sell_price"
down_revision = "20261014_24_stock_card_nota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE stock_cards ADD COLUMN IF NOT EXISTS sell_price NUMERIC(20,2)")


def downgrade() -> None:
    op.execute("ALTER TABLE stock_cards DROP COLUMN IF EXISTS sell_price")
