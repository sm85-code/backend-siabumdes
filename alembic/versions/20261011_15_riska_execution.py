"""Unit Riska permissions and expiring execution drafts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261011_15_riska_execution"
down_revision = "20261011_14_riska_permission"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_session_permissions",
        sa.Column("unit_usaha_id", sa.String(64), sa.ForeignKey("unit_usaha.id"), primary_key=True),
        sa.Column("is_allowed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table("riska_drafts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("unit_usaha_id", sa.String(64), sa.ForeignKey("unit_usaha.id"), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("preview", JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_table("riska_drafts")
    op.drop_table("ai_session_permissions")
