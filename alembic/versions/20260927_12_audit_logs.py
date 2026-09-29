"""Tabel audit_logs: jejak aksi admin sensitif (kelola akun, kunci sistem,
tutup periode, master data).

Revision ID: 20260927_12_audit_logs
Revises: 20260926_11_unit_usaha_business_type

Catatan: di deployment ini (App Platform, tanpa job alembic terpisah) skema
sebenarnya di-sync lewat modules.siabumdes.schema.ensure_schema() yang jalan
tiap boot -- file ini disediakan supaya riwayat skema di alembic/versions
tetap konsisten dengan model, untuk dev lokal/CI yang menjalankan alembic
secara eksplisit.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260927_12_audit_logs"
down_revision = "20260926_11_unit_usaha_business_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("actor_name", sa.String(255), nullable=False, server_default="-"),
        sa.Column("actor_role", sa.String(64), nullable=False, server_default="-"),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
