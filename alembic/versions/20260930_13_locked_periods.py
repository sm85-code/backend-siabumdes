"""Tabel locked_periods: kunci periode untuk non-admin (admin masih boleh koreksi).

Revision ID: 20260930_13_locked_periods
Revises: 20260927_12_audit_logs

Catatan: di deployment ini tabel dibuat oleh ensure_schema() (create_all) saat
boot; file ini menjaga riwayat alembic tetap konsisten dengan model.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260930_13_locked_periods"
down_revision = "20260927_12_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "locked_periods",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("group_code", sa.String(32), nullable=False, server_default="ALL"),
        sa.Column("locked_by", sa.String(64), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("period", "group_code", name="uq_locked_periods_period_group"),
    )
    op.create_index("ix_locked_periods_period", "locked_periods", ["period"])


def downgrade() -> None:
    op.drop_index("ix_locked_periods_period", table_name="locked_periods")
    op.drop_table("locked_periods")
