"""Unit-scoped read-only catalog credentials for marketplace ERP."""
from alembic import op

revision = "20261017_28_supplier_catalog"
down_revision = "20261016_27_doc_numbers"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS supplier_catalog_access (
        id VARCHAR(36) PRIMARY KEY, token_digest VARCHAR(64) NOT NULL UNIQUE,
        unit_usaha_id VARCHAR(36) NOT NULL, issued_by VARCHAR(64) NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ
    )""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS supplier_catalog_access")
