"""Create the application entity store and migration ledger."""
from alembic import op

revision = "20260913_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS application_entities (
        namespace VARCHAR(80) NOT NULL,
        id VARCHAR(120) NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (namespace, id)
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_application_entities_namespace_updated ON application_entities (namespace, updated_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_application_entities_payload_gin ON application_entities USING gin (payload)")
    op.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")


def downgrade() -> None:
    op.drop_table("schema_migrations")
    op.drop_index("ix_application_entities_payload_gin", table_name="application_entities")
    op.drop_index("ix_application_entities_namespace_updated", table_name="application_entities")
    op.drop_table("application_entities")
