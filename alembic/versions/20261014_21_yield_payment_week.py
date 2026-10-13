"""Placeholder for the reverted weekly Setoran revision.

Revision 20261014_21_yield_payment_week was applied in production (PR #37) and
then its file was removed by the revert in PR #38, so `alembic upgrade head`
could no longer find the stamped revision. This no-op file restores the chain.
The monthly uniqueness is restored at boot by ensure_schema(); the unused
`week` column is kept.

Revision ID: 20261014_21_yield_payment_week
Revises: 20261013_20_toko_entry_transaction
"""

revision = "20261014_21_yield_payment_week"
down_revision = "20261013_20_toko_entry_transaction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
