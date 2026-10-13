"""yield_payments.week: input mingguan Manajemen Setoran.

Revision ID: 20261014_21_yield_payment_week
Revises: 20261013_20_toko_entry_transaction

Tidak ada data yang dihapus: baris lama mendapat week=0 (bulanan). Unik
dipindah dari (partner, year, month) ke (partner, year, month, week).
Di produksi hal yang sama dijalankan ensure_schema() (_ADD_COLUMNS + _POST_DDL).
"""
from alembic import op
import sqlalchemy as sa

revision = "20261014_21_yield_payment_week"
down_revision = "20261013_20_toko_entry_transaction"
branch_labels = None
depends_on = None


def _has_table() -> bool:
    # yield_payments dibuat oleh create_all (ensure_schema), bukan revisi alembic sebelumnya.
    return sa.inspect(op.get_bind()).has_table("yield_payments")


def upgrade() -> None:
    if not _has_table():
        return
    op.add_column("yield_payments", sa.Column("week", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("uq_yield_payment_period_week", "yield_payments", ["partner_id", "year", "month", "week"], unique=True)
    op.execute("ALTER TABLE yield_payments DROP CONSTRAINT IF EXISTS uq_yield_payment_period")


def downgrade() -> None:
    # Hanya aman bila tiap mitra/bulan tinggal satu baris.
    if not _has_table():
        return
    op.drop_index("uq_yield_payment_period_week", table_name="yield_payments")
    op.create_unique_constraint("uq_yield_payment_period", "yield_payments", ["partner_id", "year", "month"])
    op.drop_column("yield_payments", "week")
