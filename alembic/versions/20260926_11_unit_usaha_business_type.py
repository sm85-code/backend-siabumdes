"""Tambah unit_usaha.business_type (jasa | perdagangan | manufaktur).

Revision ID: 20260926_11_unit_usaha_business_type
Revises: 20260926_10_stock_out_internal_use

Catatan: di deployment ini (App Platform, tanpa job alembic terpisah) skema
sebenarnya di-sync lewat modules.siabumdes.schema.ensure_schema() yang jalan
tiap boot -- file ini disediakan supaya riwayat skema di alembic/versions
tetap konsisten dengan model, untuk dev lokal/CI yang menjalankan alembic
secara eksplisit.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260926_11_unit_usaha_business_type"
down_revision = "20260926_10_stock_out_internal_use"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "unit_usaha",
        sa.Column("business_type", sa.String(20), nullable=False, server_default="jasa"),
    )


def downgrade() -> None:
    op.drop_column("unit_usaha", "business_type")
