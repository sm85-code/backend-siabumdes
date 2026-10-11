"""Deny-by-default Riska assignment for unit managers."""
from alembic import op
import sqlalchemy as sa

revision = "20261011_14_riska_permission"
down_revision = "20260930_13_locked_periods"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("riska_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("users", "riska_enabled")
