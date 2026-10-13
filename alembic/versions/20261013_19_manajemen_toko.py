"""Tabel Manajemen Toko (UU05): toko_items dan toko_entries.

Revision ID: 20261013_19_manajemen_toko
Revises: 20261012_18_riska_ai

Catatan: di deployment ini tabel juga dibuat oleh ensure_schema() (create_all)
saat boot; file ini menjaga riwayat alembic tetap konsisten dengan model.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261013_19_manajemen_toko"
down_revision = "20261012_18_riska_ai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "toko_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("jenis", sa.String(16), nullable=False, server_default="pemasukan"),
        sa.Column("keterangan", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "toko_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.String(64), sa.ForeignKey("toko_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("tanggal", sa.Date(), nullable=False),
        sa.Column("nominal", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("item_id", "period", "week", name="uq_toko_entries_item_period_week"),
    )
    op.create_index("ix_toko_entries_item_id", "toko_entries", ["item_id"])
    op.create_index("ix_toko_entries_period", "toko_entries", ["period"])


def downgrade() -> None:
    op.drop_index("ix_toko_entries_period", table_name="toko_entries")
    op.drop_index("ix_toko_entries_item_id", table_name="toko_entries")
    op.drop_table("toko_entries")
    op.drop_table("toko_items")
