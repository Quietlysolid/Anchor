"""Add execution-path indexes.

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_orders_oanda_order_id
        ON orders (oanda_order_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_trades_closed_at
        ON trades (closed_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_trades_signal_id
        ON trades (signal_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_system_events_event_type
        ON system_events (event_type)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_trades_position_id
        ON trades (position_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trades_position_id")
    op.execute("DROP INDEX IF EXISTS ix_system_events_event_type")
    op.execute("DROP INDEX IF EXISTS ix_trades_signal_id")
    op.execute("DROP INDEX IF EXISTS ix_trades_closed_at")
    op.execute("DROP INDEX IF EXISTS ix_orders_oanda_order_id")
