"""inventory_vendors.code / inventory_customers.code: kode mitra untuk nomor dokumen.

Revision ID: 20261016_26_partner_code
Revises: 20261015_25_stock_card_sell_price

Catatan: di produksi kolom ini ditambahkan oleh ensure_schema() lewat _ADD_COLUMNS.
"""

from alembic import op

revision = "20261016_26_partner_code"
down_revision = "20261015_25_stock_card_sell_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE inventory_vendors ADD COLUMN IF NOT EXISTS code VARCHAR(16)")
    op.execute("ALTER TABLE inventory_customers ADD COLUMN IF NOT EXISTS code VARCHAR(16)")


def downgrade() -> None:
    op.execute("ALTER TABLE inventory_vendors DROP COLUMN IF EXISTS code")
    op.execute("ALTER TABLE inventory_customers DROP COLUMN IF EXISTS code")
