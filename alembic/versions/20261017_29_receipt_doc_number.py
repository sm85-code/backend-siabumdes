"""Nomor nota otomatis Stock In/Out: stock_cards.doc_number + penghitung per minggu.

Revision ID: 20261017_29_receipt_doc_number
Revises: 20261017_28_supplier_catalog

Catatan: di produksi kolom/tabel dibuat ensure_schema() (_ADD_COLUMNS/_POST_DDL/create_all);
pengisian data lama (backfill) berjalan saat boot lewat backfill_receipt_numbers().
"""
from alembic import op

revision = "20261017_29_receipt_doc_number"
down_revision = "20261017_28_supplier_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE stock_cards ADD COLUMN IF NOT EXISTS doc_number VARCHAR(64)")
    op.execute("ALTER TABLE stock_cards ADD COLUMN IF NOT EXISTS doc_unit_id VARCHAR(36)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_cards_doc_number ON stock_cards (doc_unit_id, doc_number) "
        "WHERE doc_number IS NOT NULL"
    )
    op.execute("""CREATE TABLE IF NOT EXISTS inventory_receipt_counters (
        unit_usaha_id VARCHAR(36) NOT NULL, kind VARCHAR(16) NOT NULL, week_start DATE NOT NULL,
        partner_key VARCHAR(36) NOT NULL, last_seq INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (unit_usaha_id, kind, week_start, partner_key)
    )""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS inventory_receipt_counters")
    op.execute("DROP INDEX IF EXISTS uq_stock_cards_doc_number")
    op.execute("ALTER TABLE stock_cards DROP COLUMN IF EXISTS doc_unit_id")
    op.execute("ALTER TABLE stock_cards DROP COLUMN IF EXISTS doc_number")
