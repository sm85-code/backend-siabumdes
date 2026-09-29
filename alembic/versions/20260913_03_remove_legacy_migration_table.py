"""Remove the redundant pre-Alembic migration ledger."""
from alembic import op

revision = "20260913_03"
down_revision = "20260913_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS schema_migrations")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        INSERT INTO schema_migrations (version)
        SELECT 1
        WHERE NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 1)
        """
    )
