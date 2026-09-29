"""Tambah stock_cards.movement_kind (sale | internal_use).

Revision ID: 20260926_10_stock_out_internal_use
Revises: 20260925_09_bagi_hasil_config

Catatan: di deployment ini (App Platform, tanpa job alembic terpisah) skema
sebenarnya di-sync lewat modules.siabumdes.schema.ensure_schema() yang jalan
tiap boot -- file ini disediakan supaya riwayat skema di alembic/versions
tetap konsisten dengan model, untuk dev lokal/CI yang menjalankan alembic
secara eksplisit.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260926_10_stock_out_internal_use"
down_revision = "20260925_09_bagi_hasil_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stock_cards",
        sa.Column("movement_kind", sa.String(16), nullable=False, server_default="sale"),
    )


def downgrade() -> None:
    op.drop_column("stock_cards", "movement_kind")
