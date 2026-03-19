"""Pydantic response models for all API endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel


# ── System ───────────────────────────────────────────────────────────────────
class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str = "1.0.0"
    services: Dict[str, str] = {}


class SystemEventResponse(BaseModel):
    id: UUID
    event_at: datetime
    event_type: str
    severity: str
    message: str
    metadata: Optional[Dict] = None


class EngineConfigResponse(BaseModel):
    enabled: bool
    paper_only: bool
    risk_pct: float


class PilotConfigResponse(BaseModel):
    instruments: List[str]
    trend: EngineConfigResponse
    mean_reversion: EngineConfigResponse
    lcr: EngineConfigResponse
    m15: EngineConfigResponse


class RolloutConfigResponse(BaseModel):
    instruments: List[str]
    trend: EngineConfigResponse
    mean_reversion: EngineConfigResponse
    lcr: EngineConfigResponse
    m15: EngineConfigResponse
    expected_pilot: PilotConfigResponse
    max_risk_per_trade_fallback: float
    min_confluence_score: float
    min_ml_confidence: float


# ── Market Data ───────────────────────────────────────────────────────────────
class CandleResponse(BaseModel):
    time: datetime
    instrument: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float]
    spread_avg: Optional[float]


class LivePriceResponse(BaseModel):
    instrument: str
    bid: float
    ask: float
    mid: float
    time: datetime
    spread_pips: float


# ── Signals ───────────────────────────────────────────────────────────────────
class SignalResponse(BaseModel):
    id: UUID
    created_at: datetime
    instrument: str
    direction: Optional[str]
    confluence_score: float
    rsi_score: float
    bb_kc_score: float
    adx_score: float
    sr_score: float
    mtf_score: float
    csi_score: float
    ml_confidence: Optional[float]
    regime_state: Optional[str]
    session: Optional[str]
    suppressed: bool
    suppression_reason: Optional[str]
    metadata: Optional[Dict] = None


# ── Positions ─────────────────────────────────────────────────────────────────
class PositionResponse(BaseModel):
    id: UUID
    instrument: str
    direction: str
    units: int
    avg_entry_price: float
    unrealized_pl: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    oanda_trade_id: Optional[str]
    status: str
    opened_at: Optional[datetime] = None


class TradeResponse(BaseModel):
    id: UUID
    instrument: str
    direction: str
    units: float
    entry_price: float
    exit_price: Optional[float]
    opened_at: datetime
    closed_at: Optional[datetime]
    net_pl: float
    gross_pl: float
    commission: float
    max_adverse_excursion: Optional[float]
    max_favorable_excursion: Optional[float]
    close_reason: Optional[str]
    regime_at_entry: Optional[str]
    session_at_entry: Optional[str]
    expected_entry_price: Optional[float]
    fill_price: Optional[float]
    entry_slippage_pips: Optional[float]
    spread_at_fill: Optional[float]
    fill_at: Optional[datetime]


# ── Orders ────────────────────────────────────────────────────────────────────
class OrderResponse(BaseModel):
    id: UUID
    created_at: datetime
    instrument: str
    direction: str
    order_type: str
    units: int
    limit_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    state: str
    oanda_order_id: Optional[str]
    filled_units: int
    avg_fill_price: Optional[float]
    signal_id: Optional[UUID]


class OrderEventResponse(BaseModel):
    id: UUID
    event_at: datetime
    from_state: str
    to_state: str
    message: Optional[str]


# ── Performance ───────────────────────────────────────────────────────────────
class PerformanceSummaryResponse(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    net_pnl: float
    net_pnl_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    avg_win: float
    avg_loss: float
    avg_trade_duration_hours: Optional[float]


class EquityCurvePoint(BaseModel):
    time: datetime
    balance: float
    equity: float
    drawdown_pct: float


class MonteCarloResponse(BaseModel):
    simulations: int
    median_return_pct: float
    p5_return_pct: float
    p95_return_pct: float
    median_max_drawdown_pct: float
    p95_max_drawdown_pct: float
    risk_of_ruin_pct: float
    expected_sharpe: float


class EdgeDecayResponse(BaseModel):
    rolling_sharpe: float
    trade_count: int
    warning: bool
    halt: bool
    message: str


class SlippageSurfaceRow(BaseModel):
    instrument: str
    session: str
    median_pips: float
    p95_pips: float
    count: int


# ── Regime ────────────────────────────────────────────────────────────────────
class RegimeResponse(BaseModel):
    instrument: str
    regime: str
    confidence: float
    detected_at: datetime


# ── Calendar ─────────────────────────────────────────────────────────────────
class EconomicEventResponse(BaseModel):
    id: UUID
    event_time: datetime
    currency: str
    impact: str
    title: str
    source: Optional[str]
    actual: Optional[str]
    forecast: Optional[str]
    previous: Optional[str]


class CalendarFilterResponse(BaseModel):
    instrument: str
    suppressed: bool
    reason: Optional[str]
    events: List[EconomicEventResponse]
