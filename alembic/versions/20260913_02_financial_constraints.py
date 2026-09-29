"""Apply financial payload constraint to existing installations."""
from alembic import op

revision = "20260913_02"
down_revision = "20260913_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'application_entities'::regclass
              AND conname = 'ck_entities_amount_type'
        ) THEN
            ALTER TABLE application_entities
            ADD CONSTRAINT ck_entities_amount_type
            CHECK (NOT (payload ? 'amount') OR jsonb_typeof(payload->'amount') IN ('number', 'string'));
        END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE application_entities DROP CONSTRAINT IF EXISTS ck_entities_amount_type")
