"""Initial schema — all tables

Revision ID: 0001
Revises:
Create Date: 2026-03-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── market_data ──────────────────────────────────────────────────────────────
    op.create_table(
        "market_data",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument", sa.String(12), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.Integer()),
        sa.Column("spread_avg", sa.Numeric(10, 5)),
        sa.Column("source", sa.String(20), nullable=False, server_default="ibkr"),
    )
    op.create_index("ix_market_data_instrument_tf_time", "market_data", ["instrument", "timeframe", "time"])
    # UNIQUE constraint required for ON CONFLICT (time, instrument, timeframe) DO NOTHING.
    # TimescaleDB requires the partition key (time) to be part of any unique index,
    # which it is here — so this works on both plain Postgres and TimescaleDB.
    op.create_index(
        "uq_market_data_time_instrument_tf",
        "market_data",
        ["time", "instrument", "timeframe"],
        unique=True,
    )

    # TimescaleDB hypertable — only runs if extension is available
    try:
        op.execute("SELECT create_hypertable('market_data', 'time', if_not_exists => TRUE)")
    except Exception:
        pass  # fallback for plain postgres without timescaledb

    # ── tick_data ────────────────────────────────────────────────────────────────
    op.create_table(
        "tick_data",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False, primary_key=True),
        sa.Column("instrument", sa.String(12), nullable=False, primary_key=True),
        sa.Column("bid", sa.Numeric(18, 6), nullable=False),
        sa.Column("ask", sa.Numeric(18, 6), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="ibkr"),
    )

    try:
        op.execute("SELECT create_hypertable('tick_data', 'time', if_not_exists => TRUE)")
    except Exception:
        pass

    # ── signals ──────────────────────────────────────────────────────────────────
    op.create_table(
        "signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("instrument", sa.String(12), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False, server_default="H1"),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("confluence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("rsi_score", sa.Numeric(5, 4)),
        sa.Column("bb_kc_score", sa.Numeric(5, 4)),
        sa.Column("adx_score", sa.Numeric(5, 4)),
        sa.Column("sr_score", sa.Numeric(5, 4)),
        sa.Column("mtf_score", sa.Numeric(5, 4)),
        sa.Column("csi_score", sa.Numeric(5, 4)),
        sa.Column("ml_confidence", sa.Numeric(5, 4)),
        sa.Column("regime_state", sa.String(12)),
        sa.Column("session", sa.String(8)),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("suppression_reason", sa.Text()),
        sa.Column("signal_metadata", postgresql.JSONB()),
    )
    op.create_index("ix_signals_instrument_created", "signals", ["instrument", "created_at"])

    # ── orders ───────────────────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("signal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("signals.id")),
        sa.Column("instrument", sa.String(12), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("order_type", sa.String(12), nullable=False, server_default="MARKET"),
        sa.Column("requested_units", sa.Numeric(18, 2), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("broker_order_id", sa.String(64)),
        sa.Column("limit_price", sa.Numeric(18, 6)),
        sa.Column("stop_price", sa.Numeric(18, 6)),
        sa.Column("take_profit", sa.Numeric(18, 6)),
        sa.Column("stop_loss", sa.Numeric(18, 6)),
        sa.Column("trailing_stop_distance", sa.Numeric(18, 6)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("reject_reason", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
    )
    op.create_index("ix_orders_instrument_state", "orders", ["instrument", "state"])

    # ── order_events ─────────────────────────────────────────────────────────────
    op.create_table(
        "order_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("from_state", sa.String(16)),
        sa.Column("to_state", sa.String(16), nullable=False),
        sa.Column("event_data", postgresql.JSONB()),
    )

    # ── fills ────────────────────────────────────────────────────────────────────
    op.create_table(
        "fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("fill_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument", sa.String(12), nullable=False),
        sa.Column("units_filled", sa.Numeric(18, 2), nullable=False),
        sa.Column("fill_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("expected_price", sa.Numeric(18, 6)),
        sa.Column("slippage_pips", sa.Numeric(10, 4)),
        sa.Column("spread_at_fill", sa.Numeric(10, 5)),
        sa.Column("commission", sa.Numeric(18, 6), server_default="0"),
        sa.Column("pl_realized", sa.Numeric(18, 6)),
        sa.Column("pl_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("broker_fill_id", sa.String(64)),
        sa.Column("metadata", postgresql.JSONB()),
    )

    # ── positions ────────────────────────────────────────────────────────────────
    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("instrument", sa.String(12), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("units", sa.Numeric(18, 2), nullable=False),
        sa.Column("avg_entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("current_price", sa.Numeric(18, 6)),
        sa.Column("unrealized_pl", sa.Numeric(18, 6)),
        sa.Column("realized_pl", sa.Numeric(18, 6), server_default="0"),
        sa.Column("stop_loss", sa.Numeric(18, 6)),
        sa.Column("take_profit", sa.Numeric(18, 6)),
        sa.Column("trailing_stop_distance", sa.Numeric(18, 6)),
        sa.Column("broker_trade_id", sa.String(64), unique=True),
        sa.Column("status", sa.String(8), nullable=False, server_default="OPEN"),
        sa.Column("signal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("signals.id")),
    )
    op.create_index("ix_positions_status_instrument", "positions", ["status", "instrument"])

    # ── trades ───────────────────────────────────────────────────────────────────
    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("positions.id"), nullable=False),
        sa.Column("instrument", sa.String(12), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("units", sa.Numeric(18, 2), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer()),
        sa.Column("gross_pl", sa.Numeric(18, 6), nullable=False),
        sa.Column("commission", sa.Numeric(18, 6), server_default="0"),
        sa.Column("net_pl", sa.Numeric(18, 6), nullable=False),
        sa.Column("pl_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("max_adverse_excursion", sa.Numeric(18, 6)),
        sa.Column("max_favorable_excursion", sa.Numeric(18, 6)),
        sa.Column("close_reason", sa.String(32)),
        sa.Column("regime_at_entry", sa.String(12)),
        sa.Column("session_at_entry", sa.String(8)),
        sa.Column("signal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("signals.id")),
    )
    op.create_index("ix_trades_instrument_closed", "trades", ["instrument", "closed_at"])

    # ── equity_curve ─────────────────────────────────────────────────────────────
    op.create_table(
        "equity_curve",
        sa.Column("time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("account_balance", sa.Numeric(18, 6), nullable=False),
        sa.Column("account_equity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unrealized_pl", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("open_position_count", sa.Integer(), server_default="0"),
        sa.Column("daily_pl", sa.Numeric(18, 6)),
        sa.Column("peak_equity", sa.Numeric(18, 6)),
        sa.Column("drawdown_pct", sa.Numeric(8, 4)),
    )

    try:
        op.execute("SELECT create_hypertable('equity_curve', 'time', if_not_exists => TRUE)")
    except Exception:
        pass

    # ── system_events ─────────────────────────────────────────────────────────────
    op.create_table(
        "system_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False, server_default="INFO"),
        sa.Column("component", sa.String(32), nullable=False, server_default="SYSTEM"),
        sa.Column("message", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
    )

    # ── regime_history ───────────────────────────────────────────────────────────
    op.create_table(
        "regime_history",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False, primary_key=True),
        sa.Column("instrument", sa.String(12), primary_key=True),
        sa.Column("regime", sa.String(12), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("transition_from", sa.String(12)),
    )

    try:
        op.execute("SELECT create_hypertable('regime_history', 'time', if_not_exists => TRUE)")
    except Exception:
        pass

    # ── slippage_records ─────────────────────────────────────────────────────────
    op.create_table(
        "slippage_records",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("instrument", sa.String(12), nullable=False),
        sa.Column("session", sa.String(8), nullable=False),
        sa.Column("lot_size", sa.Numeric(18, 2), nullable=False),
        sa.Column("expected_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("fill_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("slippage_pips", sa.Numeric(10, 4), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("slippage_records")
    op.drop_table("regime_history")
    op.drop_table("system_events")
    op.drop_table("equity_curve")
    op.drop_table("trades")
    op.drop_table("positions")
    op.drop_table("fills")
    op.drop_table("order_events")
    op.drop_table("orders")
    op.drop_table("signals")
    op.drop_table("tick_data")
    op.drop_table("market_data")

