"""Add persistent broker stop tracking to positions.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("ibkr_stop_order_id", sa.String(length=64), nullable=True))
    op.add_column("positions", sa.Column("stop_attached_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "stop_attached_at")
    op.drop_column("positions", "ibkr_stop_order_id")
