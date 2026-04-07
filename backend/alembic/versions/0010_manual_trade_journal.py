"""Add persistent manual trade journal.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_trade_journal",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("action_key", sa.String(length=160), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("instrument", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("contracts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("anchor_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anchor_reference_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("anchor_stop_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("anchor_entry_note", sa.Text(), nullable=True),
        sa.Column("anchor_exit_note", sa.Text(), nullable=True),
        sa.Column("taken", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("fill_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("stop_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_key"),
    )
    op.alter_column("manual_trade_journal", "contracts", server_default=None)
    op.create_index("ix_manual_trade_journal_updated_at", "manual_trade_journal", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_manual_trade_journal_updated_at", table_name="manual_trade_journal")
    op.drop_table("manual_trade_journal")
