"""Add broker provenance fields to trades.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("close_source", sa.String(length=32), nullable=True))
    op.add_column("trades", sa.Column("broker_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("trades", sa.Column("broker_order_id", sa.String(length=64), nullable=True))
    op.add_column("trades", sa.Column("broker_fill_id", sa.String(length=64), nullable=True))
    op.execute("UPDATE trades SET close_source = 'reconciliation', broker_verified = false WHERE close_source IS NULL")
    op.alter_column("trades", "broker_verified", server_default=None)


def downgrade() -> None:
    op.drop_column("trades", "broker_fill_id")
    op.drop_column("trades", "broker_order_id")
    op.drop_column("trades", "broker_verified")
    op.drop_column("trades", "close_source")
