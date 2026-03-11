"""Add partial_tp_done and initial_units to positions table.

Revision ID: 0004
Revises: 0003_widen_signal_columns
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003_widen_signal_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("partial_tp_done", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("positions", sa.Column("initial_units", sa.Numeric(18, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "initial_units")
    op.drop_column("positions", "partial_tp_done")
