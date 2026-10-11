"""Bounded optional Riska model calls; accounting tables remain unchanged."""
from alembic import op
import sqlalchemy as sa

revision = '20261012_18_riska_ai'
down_revision = '20261011_17_riska_assignments'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('riska_ai_budget', sa.Column('day', sa.String(10), primary_key=True), sa.Column('calls', sa.Integer(), nullable=False), sa.Column('reserved_usd', sa.Numeric(12, 6), nullable=False))
    op.create_table('riska_ai_calls', sa.Column('id', sa.String(64), primary_key=True), sa.Column('user_id', sa.String(64), sa.ForeignKey('users.id'), nullable=False), sa.Column('model', sa.String(64), nullable=False), sa.Column('status', sa.String(20), nullable=False), sa.Column('input_tokens', sa.Integer(), nullable=False), sa.Column('output_tokens', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('riska_ai_calls')
    op.drop_table('riska_ai_budget')
