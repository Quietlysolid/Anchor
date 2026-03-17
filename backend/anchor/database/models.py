import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from anchor.database.engine import Base


# ── Enums ──────────────────────────────────────────────────────────────────────

class OrderState(str, enum.Enum):
    PENDING       = "PENDING"
    SUBMITTED     = "SUBMITTED"
    ACKNOWLEDGED  = "ACKNOWLEDGED"
    PARTIAL       = "PARTIAL"
    FILLED        = "FILLED"
    CANCELLED     = "CANCELLED"
    REJECTED      = "REJECTED"
    EXPIRED       = "EXPIRED"


class Direction(str, enum.Enum):
    LONG  = "LONG"
    SHORT = "SHORT"


class PositionStatus(str, enum.Enum):
    OPEN   = "OPEN"
    CLOSED = "CLOSED"


class EventSeverity(str, enum.Enum):
    INFO     = "INFO"
    WARN     = "WARN"
    ERROR    = "ERROR"
    CRITICAL = "CRITICAL"


# ── Models ─────────────────────────────────────────────────────────────────────

class MarketData(Base):
    __tablename__ = "market_data"
    __table_args__ = (
        # Required so ON CONFLICT (time, instrument, timeframe) DO NOTHING works.
        # TimescaleDB requires the partition key (time) to be part of the unique index.
        UniqueConstraint("time", "instrument", "timeframe", name="uq_market_data_time_instrument_tf"),
    )

    id:         Mapped[int]      = mapped_column(BigInteger, primary_key=True)
    time:       Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    instrument: Mapped[str]      = mapped_column(String(12), nullable=False)
    timeframe:  Mapped[str]      = mapped_column(String(4), nullable=False)
    open:       Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    high:       Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    low:        Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    close:      Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    volume:     Mapped[int | None]     = mapped_column(Integer)
    spread_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    source:     Mapped[str]      = mapped_column(String(20), nullable=False, default="oanda")


class TickData(Base):
    __tablename__ = "tick_data"

    time:       Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    instrument: Mapped[str]      = mapped_column(String(12), primary_key=True)
    bid:        Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    ask:        Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    source:     Mapped[str]      = mapped_column(String(20), nullable=False, default="oanda")


class Signal(Base):
    __tablename__ = "signals"

    id:                Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at:        Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    instrument:        Mapped[str]             = mapped_column(String(12), nullable=False)
    timeframe:         Mapped[str]             = mapped_column(String(4), nullable=False, default="H1")
    direction:         Mapped[str]             = mapped_column(String(5), nullable=False)
    confluence_score:  Mapped[Decimal]         = mapped_column(Numeric(5, 4), nullable=False)
    rsi_score:         Mapped[Decimal | None]  = mapped_column(Numeric(5, 4))
    bb_kc_score:       Mapped[Decimal | None]  = mapped_column(Numeric(5, 4))
    adx_score:         Mapped[Decimal | None]  = mapped_column(Numeric(5, 4))
    sr_score:          Mapped[Decimal | None]  = mapped_column(Numeric(5, 4))
    mtf_score:         Mapped[Decimal | None]  = mapped_column(Numeric(5, 4))
    csi_score:         Mapped[Decimal | None]  = mapped_column(Numeric(5, 4))
    ml_confidence:     Mapped[Decimal | None]  = mapped_column(Numeric(5, 4))
    regime_state:      Mapped[str | None]      = mapped_column(String(16))
    session:           Mapped[str | None]      = mapped_column(String(32))
    suppressed:        Mapped[bool]            = mapped_column(Boolean, nullable=False, default=False)
    suppression_reason: Mapped[str | None]     = mapped_column(Text)
    signal_metadata:   Mapped[dict | None]     = mapped_column(JSONB)

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="signal")


class Order(Base):
    __tablename__ = "orders"

    id:                     Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at:             Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    signal_id:              Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("signals.id"))
    instrument:             Mapped[str]             = mapped_column(String(12), nullable=False)
    direction:              Mapped[str]             = mapped_column(String(5), nullable=False)
    order_type:             Mapped[str]             = mapped_column(String(12), nullable=False, default="MARKET")
    requested_units:        Mapped[Decimal]         = mapped_column(Numeric(18, 2), nullable=False)
    state:                  Mapped[str]             = mapped_column(Enum(OrderState, name="order_state", create_type=False), nullable=False, default=OrderState.PENDING)
    oanda_order_id:         Mapped[str | None]      = mapped_column(String(64))
    limit_price:            Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    stop_price:             Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    take_profit:            Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    stop_loss:              Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    trailing_stop_distance: Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    submitted_at:           Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at:        Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at:              Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at:           Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason:          Mapped[str | None]      = mapped_column(Text)
    metadata_:              Mapped[dict | None]     = mapped_column("metadata", JSONB)

    signal:  Mapped["Signal | None"] = relationship("Signal", back_populates="orders")
    events:  Mapped[list["OrderEvent"]] = relationship("OrderEvent", back_populates="order")
    fills:   Mapped[list["Fill"]] = relationship("Fill", back_populates="order")


class OrderEvent(Base):
    __tablename__ = "order_events"

    id:         Mapped[int]            = mapped_column(BigInteger, primary_key=True)
    order_id:   Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    event_at:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=func.now())
    from_state: Mapped[str | None]     = mapped_column(String(16))
    to_state:   Mapped[str]            = mapped_column(String(16), nullable=False)
    event_data: Mapped[dict | None]    = mapped_column(JSONB)

    order: Mapped["Order"] = relationship("Order", back_populates="events")


class Fill(Base):
    __tablename__ = "fills"

    id:             Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id:       Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    fill_at:        Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False)
    instrument:     Mapped[str]            = mapped_column(String(12), nullable=False)
    units_filled:   Mapped[Decimal]        = mapped_column(Numeric(18, 2), nullable=False)
    fill_price:     Mapped[Decimal]        = mapped_column(Numeric(18, 6), nullable=False)
    expected_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    slippage_pips:  Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    spread_at_fill: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    commission:     Mapped[Decimal]        = mapped_column(Numeric(18, 6), default=0)
    pl_realized:    Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    pl_currency:    Mapped[str]            = mapped_column(String(3), nullable=False, default="USD")
    oanda_fill_id:  Mapped[str | None]     = mapped_column(String(64))
    metadata_:      Mapped[dict | None]    = mapped_column("metadata", JSONB)

    order: Mapped["Order"] = relationship("Order", back_populates="fills")


class Position(Base):
    __tablename__ = "positions"

    id:                     Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opened_at:              Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at:              Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    instrument:             Mapped[str]             = mapped_column(String(12), nullable=False)
    direction:              Mapped[str]             = mapped_column(String(5), nullable=False)
    units:                  Mapped[Decimal]         = mapped_column(Numeric(18, 2), nullable=False)
    avg_entry_price:        Mapped[Decimal]         = mapped_column(Numeric(18, 6), nullable=False)
    current_price:          Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    unrealized_pl:          Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    realized_pl:            Mapped[Decimal]         = mapped_column(Numeric(18, 6), default=0)
    stop_loss:              Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    take_profit:            Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    trailing_stop_distance: Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    oanda_trade_id:         Mapped[str | None]      = mapped_column(String(64), unique=True)
    status:                 Mapped[str]             = mapped_column(String(8), nullable=False, default="OPEN")
    partial_tp_done:        Mapped[bool]            = mapped_column(Boolean, nullable=False, default=False)
    initial_units:          Mapped[Decimal | None]  = mapped_column(Numeric(18, 2))  # units at open, before partial close
    signal_id:              Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("signals.id"))

    trade: Mapped["Trade | None"] = relationship("Trade", back_populates="position", uselist=False)


class Trade(Base):
    __tablename__ = "trades"

    id:                      Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    position_id:             Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("positions.id"), nullable=False)
    instrument:              Mapped[str]             = mapped_column(String(12), nullable=False)
    direction:               Mapped[str]             = mapped_column(String(5), nullable=False)
    units:                   Mapped[Decimal]         = mapped_column(Numeric(18, 2), nullable=False)
    entry_price:             Mapped[Decimal]         = mapped_column(Numeric(18, 6), nullable=False)
    exit_price:              Mapped[Decimal]         = mapped_column(Numeric(18, 6), nullable=False)
    opened_at:               Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at:               Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes:        Mapped[int | None]      = mapped_column(Integer)
    gross_pl:                Mapped[Decimal]         = mapped_column(Numeric(18, 6), nullable=False)
    commission:              Mapped[Decimal]         = mapped_column(Numeric(18, 6), default=0)
    net_pl:                  Mapped[Decimal]         = mapped_column(Numeric(18, 6), nullable=False)
    pl_currency:             Mapped[str]             = mapped_column(String(3), nullable=False, default="USD")
    max_adverse_excursion:   Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    max_favorable_excursion: Mapped[Decimal | None]  = mapped_column(Numeric(18, 6))
    close_reason:            Mapped[str | None]      = mapped_column(String(32))
    regime_at_entry:         Mapped[str | None]      = mapped_column(String(16))
    session_at_entry:        Mapped[str | None]      = mapped_column(String(16))
    signal_id:               Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("signals.id"))

    position: Mapped["Position"] = relationship("Position", back_populates="trade")


class EquityCurvePoint(Base):
    __tablename__ = "equity_curve"

    time:                Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    account_balance:     Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    account_equity:      Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    unrealized_pl:       Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False, default=0)
    open_position_count: Mapped[int]      = mapped_column(Integer, default=0)
    daily_pl:            Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    peak_equity:         Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    drawdown_pct:        Mapped[Decimal | None] = mapped_column(Numeric(8, 4))


class SystemEvent(Base):
    __tablename__ = "system_events"

    id:         Mapped[int]         = mapped_column(BigInteger, primary_key=True)
    event_at:   Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_type: Mapped[str]         = mapped_column(String(32), nullable=False)
    severity:   Mapped[str]         = mapped_column(String(8), nullable=False, default="INFO")
    component:  Mapped[str]         = mapped_column(String(32), nullable=False, default="SYSTEM")
    message:    Mapped[str | None]  = mapped_column(Text)
    metadata_:  Mapped[dict | None] = mapped_column("metadata", JSONB)


class EconomicEvent(Base):
    __tablename__ = "economic_calendar"

    id:          Mapped[int]         = mapped_column(BigInteger, primary_key=True)
    event_time:  Mapped[datetime]    = mapped_column(DateTime(timezone=True), nullable=False)
    currency:    Mapped[str]         = mapped_column(String(3), nullable=False)
    impact:      Mapped[str]         = mapped_column(String(6), nullable=False)
    event_name:  Mapped[str]         = mapped_column(Text, nullable=False)
    forecast:    Mapped[str | None]  = mapped_column(Text)
    previous:    Mapped[str | None]  = mapped_column(Text)
    actual:      Mapped[str | None]  = mapped_column(Text)
    imported_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())


class RegimeHistory(Base):
    __tablename__ = "regime_history"

    time:             Mapped[datetime]     = mapped_column(DateTime(timezone=True), primary_key=True)
    instrument:       Mapped[str | None]   = mapped_column(String(12), primary_key=True)
    regime:           Mapped[str]          = mapped_column(String(16), nullable=False)
    confidence:       Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    transition_from:  Mapped[str | None]   = mapped_column(String(16))


class IntelligenceReport(Base):
    """LLM-generated pre/post-session briefs and weekly synthesis."""
    __tablename__ = "intelligence_reports"

    id:                Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at:        Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    report_type:       Mapped[str]             = mapped_column(String(16), nullable=False)  # PRESESSION | POSTSESSION | WEEKLY
    content:           Mapped[str]             = mapped_column(Text, nullable=False)
    context_snapshot:  Mapped[dict | None]     = mapped_column(JSONB)
    delivered_telegram: Mapped[bool]           = mapped_column(Boolean, nullable=False, default=False)
    tokens_used:       Mapped[int | None]      = mapped_column(Integer)


class SlippageRecord(Base):
    __tablename__ = "slippage_records"

    id:             Mapped[int]      = mapped_column(BigInteger, primary_key=True)
    recorded_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    instrument:     Mapped[str]      = mapped_column(String(12), nullable=False)
    session:        Mapped[str]      = mapped_column(String(16), nullable=False)
    lot_size:       Mapped[Decimal]  = mapped_column(Numeric(18, 2), nullable=False)
    expected_price: Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    fill_price:     Mapped[Decimal]  = mapped_column(Numeric(18, 6), nullable=False)
    slippage_pips:  Mapped[Decimal]  = mapped_column(Numeric(10, 4), nullable=False)
