"""Add spread_at_fill to trades table.

Backfills from fills.spread_at_fill via the signal_id chain:
  trades.signal_id → orders.signal_id → fills.order_id

The fills table is empty as of this migration (OANDA fills not yet
being persisted), so the backfill UPDATE returns 0 rows. It is included
so it runs automatically once fills start being recorded and the column
is added retroactively via a manual re-run.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("spread_at_fill", sa.Numeric(10, 5), nullable=True),
    )
    # Backfill: copy spread from the entry fill linked via signal_id.
    # Takes the earliest fill per trade (entry fill) to avoid using exit fills.
    op.execute("""
        UPDATE trades t
        SET spread_at_fill = f.spread_at_fill
        FROM (
            SELECT DISTINCT ON (o.signal_id)
                o.signal_id,
                f.spread_at_fill
            FROM fills f
            JOIN orders o ON o.id = f.order_id
            WHERE f.spread_at_fill IS NOT NULL
              AND o.signal_id IS NOT NULL
            ORDER BY o.signal_id, f.fill_at ASC
        ) f
        WHERE t.signal_id = f.signal_id
          AND t.spread_at_fill IS NULL
    """)


def downgrade() -> None:
    op.drop_column("trades", "spread_at_fill")
