"""Add unique constraint on market_data(time, instrument, timeframe)

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-09

Without this unique index, ON CONFLICT (time, instrument, timeframe) DO NOTHING
raises a PostgreSQL error because there is no constraint to reference.
TimescaleDB requires the partition key (time) to be included in any unique index,
which is satisfied here.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create unique index only if it does not already exist (idempotent).
    # The initial schema (0001) was updated to include this index, but any
    # database that already ran 0001 needs this migration to add it.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_market_data_time_instrument_tf
        ON market_data (time, instrument, timeframe)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_market_data_time_instrument_tf")
