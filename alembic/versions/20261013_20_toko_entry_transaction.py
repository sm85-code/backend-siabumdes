"""toko_entries.transaction_id: tautan ke transaksi UU05 otomatis.

Revision ID: 20261013_20_toko_entry_transaction
Revises: 20261013_19_manajemen_toko

Catatan: di produksi kolom ini ditambahkan oleh ensure_schema() lewat
_ADD_COLUMNS; file ini menjaga riwayat alembic tetap konsisten dengan model.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261013_20_toko_entry_transaction"
down_revision = "20261013_19_manajemen_toko"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "toko_entries",
        sa.Column("transaction_id", sa.String(64), sa.ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("toko_entries", "transaction_id")
