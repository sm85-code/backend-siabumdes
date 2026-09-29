"""Create isolated UU05 inventory tables.

Revision ID: 20260915_04_inventory_tables
Revises: 20260913_03_remove_legacy_migration_table
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_04_inventory_tables"
down_revision = "20260913_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "produk",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("nama_produk", sa.String(length=255), nullable=False),
        sa.Column("kategori", sa.String(length=100), nullable=False),
        sa.Column("stok_saat_ini", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("satuan", sa.String(length=20), nullable=False),
        sa.Column("harga_beli", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("harga_jual", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("unit_id", sa.String(length=50), nullable=False, server_default="UU05"),
        sa.UniqueConstraint("sku", name="uq_produk_sku"),
    )
    op.create_index("ix_produk_sku", "produk", ["sku"])
    op.create_index("ix_produk_unit_id", "produk", ["unit_id"])

    op.create_table(
        "stok_masuk",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("produk_id", sa.Integer(), nullable=False),
        sa.Column("jumlah", sa.Integer(), nullable=False),
        sa.Column("harga_beli_satuan", sa.BigInteger(), nullable=False),
        sa.Column("total_biaya", sa.BigInteger(), nullable=False),
        sa.Column("status_keuangan", sa.String(length=30), nullable=False, server_default="belum_sinkron"),
        sa.Column("unit_id", sa.String(length=50), nullable=False, server_default="UU05"),
        sa.ForeignKeyConstraint(["produk_id"], ["produk.id"]),
    )
    op.create_index("ix_stok_masuk_produk_id", "stok_masuk", ["produk_id"])
    op.create_index("ix_stok_masuk_unit_id", "stok_masuk", ["unit_id"])

    op.create_table(
        "stok_keluar",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("produk_id", sa.Integer(), nullable=False),
        sa.Column("jumlah", sa.Integer(), nullable=False),
        sa.Column("tipe_keluar", sa.String(length=30), nullable=False),
        sa.Column("keterangan", sa.String(length=500), nullable=True),
        sa.Column("unit_id", sa.String(length=50), nullable=False, server_default="UU05"),
        sa.ForeignKeyConstraint(["produk_id"], ["produk.id"]),
    )
    op.create_index("ix_stok_keluar_produk_id", "stok_keluar", ["produk_id"])
    op.create_index("ix_stok_keluar_unit_id", "stok_keluar", ["unit_id"])


def downgrade() -> None:
    op.drop_index("ix_stok_keluar_unit_id", table_name="stok_keluar")
    op.drop_index("ix_stok_keluar_produk_id", table_name="stok_keluar")
    op.drop_table("stok_keluar")
    op.drop_index("ix_stok_masuk_unit_id", table_name="stok_masuk")
    op.drop_index("ix_stok_masuk_produk_id", table_name="stok_masuk")
    op.drop_table("stok_masuk")
    op.drop_index("ix_produk_unit_id", table_name="produk")
    op.drop_index("ix_produk_sku", table_name="produk")
    op.drop_table("produk")
