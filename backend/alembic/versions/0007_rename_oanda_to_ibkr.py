"""Rename oanda_* columns to ibkr_* and update source defaults.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-30
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename broker ID columns
    op.execute("ALTER TABLE orders RENAME COLUMN oanda_order_id TO ibkr_order_id")
    op.execute("ALTER TABLE fills RENAME COLUMN oanda_fill_id TO ibkr_fill_id")
    op.execute("ALTER TABLE positions RENAME COLUMN oanda_trade_id TO ibkr_trade_id")

    # Update source column server defaults
    op.execute("ALTER TABLE market_data ALTER COLUMN source SET DEFAULT 'ibkr'")
    op.execute("ALTER TABLE tick_data ALTER COLUMN source SET DEFAULT 'ibkr'")

    # Rename the index on orders
    op.execute("ALTER INDEX IF EXISTS ix_orders_oanda_order_id RENAME TO ix_orders_ibkr_order_id")

    # Rename the unique constraint on positions
    op.execute("""
        ALTER TABLE positions
        RENAME CONSTRAINT positions_oanda_trade_id_key TO positions_ibkr_trade_id_key
    """)


def downgrade() -> None:
    op.execute("ALTER INDEX IF EXISTS ix_orders_ibkr_order_id RENAME TO ix_orders_oanda_order_id")
    op.execute("ALTER TABLE market_data ALTER COLUMN source SET DEFAULT 'oanda'")
    op.execute("ALTER TABLE tick_data ALTER COLUMN source SET DEFAULT 'oanda'")
    op.execute("ALTER TABLE positions RENAME COLUMN ibkr_trade_id TO oanda_trade_id")
    op.execute("ALTER TABLE fills RENAME COLUMN ibkr_fill_id TO oanda_fill_id")
    op.execute("ALTER TABLE orders RENAME COLUMN ibkr_order_id TO oanda_order_id")
