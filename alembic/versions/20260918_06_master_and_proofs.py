"""Core finance tables + transaction types, mitra, proofs, is_closing.

Revision ID: 20260918_06_master_and_proofs
Revises: 20260918_05_identity
"""
from alembic import op

revision = "20260918_06_master_and_proofs"
down_revision = "20260918_05_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy 01 only created application_entities. Normalized finance tables
    # must exist before mitra / transactions FKs.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS unit_usaha (
            id VARCHAR(64) PRIMARY KEY,
            code VARCHAR(20) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            revenue_scheme TEXT NOT NULL DEFAULT '',
            active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id VARCHAR(64) PRIMARY KEY,
            code VARCHAR(64) NOT NULL,
            name VARCHAR(255) NOT NULL,
            category VARCHAR(64) NOT NULL,
            subcategory VARCHAR(128) NOT NULL DEFAULT '',
            normal_balance VARCHAR(8) NOT NULL,
            parent_code VARCHAR(64),
            group_code VARCHAR(20) NOT NULL DEFAULT 'BUMDES',
            unit_usaha_id VARCHAR(64) REFERENCES unit_usaha(id) ON DELETE SET NULL,
            active BOOLEAN NOT NULL DEFAULT true,
            CONSTRAINT uq_accounts_code_group UNIQUE (code, group_code)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_accounts_code ON accounts (code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_accounts_category ON accounts (category)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_accounts_group_code ON accounts (group_code)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id VARCHAR(64) PRIMARY KEY,
            date DATE NOT NULL,
            unit_usaha_id VARCHAR(64) REFERENCES unit_usaha(id) ON DELETE SET NULL,
            transaction_type VARCHAR(64) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            amount NUMERIC(20, 2) NOT NULL,
            debit_account_code VARCHAR(64) NOT NULL,
            credit_account_code VARCHAR(64) NOT NULL,
            mitra_id VARCHAR(64),
            reference VARCHAR(128) NOT NULL DEFAULT '',
            created_by VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_closing BOOLEAN NOT NULL DEFAULT false,
            proofs JSONB NOT NULL DEFAULT '[]'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions (date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_transactions_reference ON transactions (reference)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_transactions_unit_usaha_id ON transactions (unit_usaha_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_transactions_transaction_type ON transactions (transaction_type)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_entries (
            id VARCHAR(64) PRIMARY KEY,
            transaction_id VARCHAR(64) NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
            entry_date DATE NOT NULL,
            memo TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_journal_entries_date ON journal_entries (entry_date)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_items (
            id VARCHAR(64) PRIMARY KEY,
            journal_entry_id VARCHAR(64) NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
            account_id VARCHAR(64) NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            side VARCHAR(8) NOT NULL,
            amount NUMERIC(20, 2) NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_journal_items_account ON journal_items (account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_journal_items_journal_entry_id ON journal_items (journal_entry_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_types (
            id VARCHAR(64) PRIMARY KEY,
            code VARCHAR(64) NOT NULL,
            name VARCHAR(255) NOT NULL,
            debit VARCHAR(64) NOT NULL DEFAULT '',
            credit VARCHAR(64) NOT NULL DEFAULT '',
            group_code VARCHAR(20) NOT NULL DEFAULT 'BUMDES',
            unit_codes VARCHAR(20)[] NOT NULL DEFAULT '{}',
            CONSTRAINT uq_tx_types_code_group UNIQUE (code, group_code)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_transaction_types_code ON transaction_types (code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_transaction_types_group_code ON transaction_types (group_code)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mitra (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            unit_usaha_id VARCHAR(64) REFERENCES unit_usaha(id) ON DELETE SET NULL,
            phone VARCHAR(50) NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_mitra_unit_usaha_id ON mitra (unit_usaha_id)")

    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_closing BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS proofs JSONB NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS proofs")
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS is_closing")
    op.execute("DROP TABLE IF EXISTS mitra CASCADE")
    op.execute("DROP TABLE IF EXISTS transaction_types CASCADE")
    op.execute("DROP TABLE IF EXISTS journal_items CASCADE")
    op.execute("DROP TABLE IF EXISTS journal_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS transactions CASCADE")
    op.execute("DROP TABLE IF EXISTS accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS unit_usaha CASCADE")
