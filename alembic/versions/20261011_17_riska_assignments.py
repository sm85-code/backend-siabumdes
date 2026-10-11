"""Replace unit-wide availability with revocable user-specific sessions."""
from alembic import op
import sqlalchemy as sa
from modules.siabumdes.application.riska_migration import LEGACY_ASSIGNMENTS, CONSUME_LEGACY_GRANTS

revision = '20261011_17_riska_assignments'
down_revision = '20261011_16_riska_dialogue'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('riska_assignments',
        sa.Column('user_id', sa.String(64), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('session_id', sa.String(64), nullable=False),
        sa.Column('assigned_by', sa.String(64), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('role', sa.String(64), nullable=False),
        sa.Column('unit_usaha_id', sa.String(64), sa.ForeignKey('unit_usaha.id'), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('awaiting_finish', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(LEGACY_ASSIGNMENTS)
    op.execute(CONSUME_LEGACY_GRANTS)


def downgrade():
    # Fail closed: do not restore broad grants after recipients ended their sessions.
    op.drop_table('riska_assignments')
