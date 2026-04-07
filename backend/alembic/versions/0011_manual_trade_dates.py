"""Add manual trade fill and close dates.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manual_trade_journal", sa.Column("filled_on", sa.Date(), nullable=True))
    op.add_column("manual_trade_journal", sa.Column("closed_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("manual_trade_journal", "closed_on")
    op.drop_column("manual_trade_journal", "filled_on")
