"""Proporsi bagi hasil per unit (riwayat berlaku mulai bulan) + snapshot proporsi tiap transfer.

Revision ID: 20261019_30_unit_bagi_hasil_shares
Revises: 20261017_29_receipt_doc_number

Catatan: di produksi tabel dibuat ensure_schema() (create_all) dan backfill per unit di _POST_DDL.
"""
from alembic import op

revision = "20261019_30_unit_bagi_hasil_shares"
down_revision = "20261017_29_receipt_doc_number"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS unit_bagi_hasil_shares (
            id VARCHAR(64) PRIMARY KEY,
            unit_usaha_id VARCHAR(64) NOT NULL REFERENCES unit_usaha(id) ON DELETE CASCADE,
            effective_from DATE NOT NULL,
            share_pengelola NUMERIC(5,2) NOT NULL,
            share_bumdes NUMERIC(5,2) NOT NULL,
            created_by VARCHAR(64) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ,
            CONSTRAINT uq_unit_bh_share_unit_from UNIQUE (unit_usaha_id, effective_from)
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_unit_bagi_hasil_shares_unit_usaha_id ON unit_bagi_hasil_shares (unit_usaha_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS bagi_hasil_transfer_shares (
            reference VARCHAR(128) PRIMARY KEY,
            unit_usaha_id VARCHAR(64) NOT NULL,
            period VARCHAR(7) NOT NULL,
            share_pengelola NUMERIC(5,2) NOT NULL,
            share_bumdes NUMERIC(5,2) NOT NULL,
            created_at TIMESTAMPTZ
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bagi_hasil_transfer_shares_unit_usaha_id ON bagi_hasil_transfer_shares (unit_usaha_id)")
    op.execute("""
        INSERT INTO unit_bagi_hasil_shares (id, unit_usaha_id, effective_from, share_pengelola, share_bumdes, created_by, created_at)
        SELECT md5(random()::text || u.id), u.id,
               COALESCE((SELECT date_trunc('month', min(t.date))::date FROM transactions t WHERE t.unit_usaha_id = u.id),
                        date_trunc('month', current_date)::date),
               COALESCE((SELECT share_unit_pengelola FROM org_profiles ORDER BY id LIMIT 1), 30),
               COALESCE((SELECT share_unit_bumdes FROM org_profiles ORDER BY id LIMIT 1), 70),
               'backfill', now()
        FROM unit_usaha u
        WHERE NOT EXISTS (SELECT 1 FROM unit_bagi_hasil_shares s WHERE s.unit_usaha_id = u.id)""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS bagi_hasil_transfer_shares")
    op.execute("DROP TABLE IF EXISTS unit_bagi_hasil_shares")
