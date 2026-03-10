"""widen signals.session and signals.regime_state columns

Revision ID: 0003_widen_signal_columns
Revises: 0002_market_data_unique_constraint
Create Date: 2026-03-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_widen_signal_columns"
down_revision = "0002_market_data_unique_constraint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # session was VARCHAR(8), needs VARCHAR(32) to hold values like OFF_SESSION_ASIAN
    op.alter_column(
        "signals",
        "session",
        type_=sa.String(32),
        existing_type=sa.String(8),
        existing_nullable=True,
    )
    # regime_state was VARCHAR(12), needs VARCHAR(16) to match model
    op.alter_column(
        "signals",
        "regime_state",
        type_=sa.String(16),
        existing_type=sa.String(12),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "signals",
        "session",
        type_=sa.String(8),
        existing_type=sa.String(32),
        existing_nullable=True,
    )
    op.alter_column(
        "signals",
        "regime_state",
        type_=sa.String(12),
        existing_type=sa.String(16),
        existing_nullable=True,
    )
