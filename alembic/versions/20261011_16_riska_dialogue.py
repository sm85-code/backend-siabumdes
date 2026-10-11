"""Allow server-owned Riska conversations and strategic drafts for the central entity."""
from alembic import op
import sqlalchemy as sa

revision = '20261011_16_riska_dialogue'
down_revision = '20261011_15_riska_execution'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('riska_drafts', 'unit_usaha_id', existing_type=sa.String(64), nullable=True)


def downgrade():
    # Only pending central conversations/drafts use NULL; no financial postings are deleted.
    op.execute('DELETE FROM riska_drafts WHERE unit_usaha_id IS NULL')
    op.alter_column('riska_drafts', 'unit_usaha_id', existing_type=sa.String(64), nullable=False)
