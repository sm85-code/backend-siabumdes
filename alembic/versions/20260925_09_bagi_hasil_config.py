"""Proporsi bagi hasil (org_profiles): kolom baru, dulu hardcode di closing.py/reporting.py.

Revision ID: 20260925_09_bagi_hasil_config
Revises: 20260919_08_coa_taxonomy

Catatan: di deployment ini (App Platform, tanpa job alembic terpisah) skema
sebenarnya di-sync lewat modules.siabumdes.schema.ensure_schema() yang jalan
tiap boot -- file ini disediakan supaya riwayat skema di alembic/versions
tetap konsisten dengan model, untuk dev lokal/CI yang menjalankan alembic
secara eksplisit.
"""
from alembic import op

revision = "20260925_09_bagi_hasil_config"
down_revision = "20260919_08_coa_taxonomy"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("share_pengurus", "35"),
    ("share_penasihat", "7"),
    ("share_pengawas", "5"),
    ("share_dana_sosial", "5"),
    ("share_pades", "30"),
    ("share_modal_bumdes", "18"),
    ("share_unit_pengelola", "30"),
    ("share_unit_bumdes", "70"),
]


def upgrade() -> None:
    for column, default in _COLUMNS:
        op.execute(
            f"ALTER TABLE org_profiles ADD COLUMN IF NOT EXISTS {column} NUMERIC(5,2) NOT NULL DEFAULT {default}"
        )


def downgrade() -> None:
    for column, _default in _COLUMNS:
        op.execute(f"ALTER TABLE org_profiles DROP COLUMN IF EXISTS {column}")
