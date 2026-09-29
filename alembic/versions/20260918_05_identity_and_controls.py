"""Users, system recording lock, and closed accounting periods.

Revision ID: 20260918_05_identity
Revises: 20260915_04_inventory_tables
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Must stay <= 32 chars: alembic_version.version_num is VARCHAR(32) by default.
revision = "20260918_05_identity"
down_revision = "20260915_04_inventory_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)")

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("unit_usaha_id", sa.String(length=36), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("blocked_periods", postgresql.ARRAY(sa.String(length=7)), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_unit_usaha_id", "users", ["unit_usaha_id"])

    op.create_table(
        "system_controls",
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("recording_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=36), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.execute("INSERT INTO system_controls (id, recording_locked, note) VALUES ('default', false, '')")

    op.create_table(
        "closed_periods",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("group_code", sa.String(length=20), nullable=False, server_default="BUMDES"),
        sa.Column("laba_bersih", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("entries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("closed_by", sa.String(length=36), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("period", "group_code", name="uq_closed_periods_period_group"),
    )
    op.create_index("ix_closed_periods_period", "closed_periods", ["period"])


def downgrade() -> None:
    op.drop_index("ix_closed_periods_period", table_name="closed_periods")
    op.drop_table("closed_periods")
    op.drop_table("system_controls")
    op.drop_index("ix_users_unit_usaha_id", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
