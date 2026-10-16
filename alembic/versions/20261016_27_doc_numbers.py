"""inventory_doc_numbers: urutan nomor INV/PO tetap per mitra per minggu.

Revision ID: 20261016_27_doc_numbers
Revises: 20261016_26_partner_code

Catatan: di produksi tabel dibuat create_all() dan constraint dihapus lewat _POST_DDL.
"""

from alembic import op

revision = "20261016_27_doc_numbers"
down_revision = "20261016_26_partner_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS inventory_doc_numbers (
            id VARCHAR(36) PRIMARY KEY,
            unit_usaha_id VARCHAR(36) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            week_start DATE NOT NULL,
            partner_id VARCHAR(36) NOT NULL,
            seq INTEGER NOT NULL,
            created_at TIMESTAMPTZ,
            CONSTRAINT uq_doc_number_partner UNIQUE (unit_usaha_id, kind, week_start, partner_id),
            CONSTRAINT uq_doc_number_seq UNIQUE (unit_usaha_id, kind, week_start, seq)
        )"""
    )
    op.execute("ALTER TABLE inventory_documents DROP CONSTRAINT IF EXISTS inventory_documents_number_key")
    op.execute("CREATE INDEX IF NOT EXISTS ix_inventory_documents_number ON inventory_documents (number)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inventory_doc_numbers")
