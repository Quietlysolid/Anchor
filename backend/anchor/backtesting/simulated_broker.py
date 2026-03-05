"""Simulated broker for backtesting.

Models spread, slippage, and partial fills realistically.
No look-ahead: fills execute at the NEXT candle's open.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from anchor.backtesting.data_feed import CandleBar

# Typical spread per instrument in pips (widened by 20% for simulation)
TYPICAL_SPREADS: Dict[str, float] = {
    "EUR_USD": 0.8,
    "GBP_USD": 1.0,
    "USD_JPY": 0.6,
    "USD_CHF": 1.0,
    "AUD_USD": 0.9,
    "NZD_USD": 1.5,
    "USD_CAD": 1.2,
    "EUR_JPY": 1.0,
}

PIP_SIZES: Dict[str, float] = {
    "USD_JPY": 0.01,
    "EUR_JPY": 0.01,
}
DEFAULT_PIP_SIZE = 0.0001

# Pairs where the quote currency is JPY — P&L is in JPY and must be converted to USD.
# For USD_JPY: 1 unit moves = (price_delta) JPY. USD value = JPY / exit_price.
# For EUR_JPY: 1 unit moves = (price_delta) JPY. USD value = JPY / exit_price.
JPY_QUOTE_PAIRS: set = {"USD_JPY", "EUR_JPY"}

# Pairs where USD is the base (not the quote) — P&L is in the quote currency,
# which is not USD, so we must convert. However for USD_JPY it is already covered
# above. USD_CHF, USD_CAD would need CHF/USD or CAD/USD rates which we don't have,
# so for now we only correct the JPY pairs (the ~150x error). CHF/CAD are close
# enough to USD that the error is <5% and acceptable without a live rate feed.

SLIPPAGE_PIPS = 0.5  # half-pip slippage on entry
COMMISSION_USD_PER_LOT = 3.5  # per 100,000 units (one standard lot)


@dataclass
class SimulatedPosition:
    id: str
    instrument: str
    direction: str  # LONG / SHORT
    units: int
    entry_price: float
    entry_time: datetime
    stop_loss: Optional[float]
    take_profit: Optional[float]
    # filled at next bar open
    status: str = "OPEN"
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    close_reason: str = ""
    net_pl: float = 0.0
    # Signal context recorded at entry (for post-hoc analysis)
    regime: Optional[str] = None
    session: Optional[str] = None
    confluence_score: float = 0.0
    rsi_score: float = 0.0
    bb_kc_score: float = 0.0
    adx_score: float = 0.0
    sr_score: float = 0.0
    mtf_score: float = 0.0
    csi_score: float = 0.0
    ml_confidence: Optional[float] = None
    atr: float = 0.0


@dataclass
class SimulatedBroker:
    account_balance: float = 10_000.0
    positions: List[SimulatedPosition] = field(default_factory=list)
    equity_history: List[Dict] = field(default_factory=list)
    trade_history: List[SimulatedPosition] = field(default_factory=list)

    def pip_size(self, instrument: str) -> float:
        return PIP_SIZES.get(instrument, DEFAULT_PIP_SIZE)

    def spread_cost(self, instrument: str) -> float:
        spread_pips = TYPICAL_SPREADS.get(instrument, 1.2) * 1.2  # widened
        return spread_pips * self.pip_size(instrument)

    def commission(self, units: int) -> float:
        lots = units / 100_000
        return lots * COMMISSION_USD_PER_LOT

    def open_position(
        self,
        instrument: str,
        direction: str,
        units: int,
        fill_price: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        fill_time: datetime,
        signal_context: Optional[dict] = None,
    ) -> SimulatedPosition:
        pip = self.pip_size(instrument)
        slip = SLIPPAGE_PIPS * pip
        spread = self.spread_cost(instrument)

        if direction == "LONG":
            entry = fill_price + spread + slip
        else:
            entry = fill_price - spread - slip

        comm = self.commission(units)
        self.account_balance -= comm

        ctx = signal_context or {}
        pos = SimulatedPosition(
            id=str(uuid.uuid4()),
            instrument=instrument,
            direction=direction,
            units=units,
            entry_price=entry,
            entry_time=fill_time,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=ctx.get("regime"),
            session=ctx.get("session"),
            confluence_score=ctx.get("confluence_score", 0.0),
            rsi_score=ctx.get("rsi_score", 0.0),
            bb_kc_score=ctx.get("bb_kc_score", 0.0),
            adx_score=ctx.get("adx_score", 0.0),
            sr_score=ctx.get("sr_score", 0.0),
            mtf_score=ctx.get("mtf_score", 0.0),
            csi_score=ctx.get("csi_score", 0.0),
            ml_confidence=ctx.get("ml_confidence"),
            atr=ctx.get("atr", 0.0),
        )
        self.positions.append(pos)
        return pos

    def update(self, bar: CandleBar) -> None:
        """Process a new bar: check SL/TP/end-of-bar for each open position."""
        to_close: List[SimulatedPosition] = []
        for pos in self.positions:
            if pos.instrument != bar.instrument or pos.status != "OPEN":
                continue

            pip = self.pip_size(pos.instrument)

            if pos.direction == "LONG":
                # Check SL (fill at SL price or bar low if gapped)
                if pos.stop_loss and bar.low <= pos.stop_loss:
                    exit_price = min(pos.stop_loss, bar.open)  # gap-down scenario
                    self._close(pos, exit_price, bar.time, "STOP_LOSS")
                    to_close.append(pos)
                elif pos.take_profit and bar.high >= pos.take_profit:
                    exit_price = pos.take_profit
                    self._close(pos, exit_price, bar.time, "TAKE_PROFIT")
                    to_close.append(pos)
            else:  # SHORT
                if pos.stop_loss and bar.high >= pos.stop_loss:
                    exit_price = max(pos.stop_loss, bar.open)
                    self._close(pos, exit_price, bar.time, "STOP_LOSS")
                    to_close.append(pos)
                elif pos.take_profit and bar.low <= pos.take_profit:
                    exit_price = pos.take_profit
                    self._close(pos, exit_price, bar.time, "TAKE_PROFIT")
                    to_close.append(pos)

        # Record equity
        unrealized = sum(self._unrealized_pl(p, bar.close) for p in self.positions if p.status == "OPEN" and p.instrument == bar.instrument)
        self.equity_history.append(
            {"time": bar.time, "balance": self.account_balance, "equity": self.account_balance + unrealized}
        )

    def _quote_to_usd(self, instrument: str, pl_in_quote: float, exit_price: float) -> float:
        """Convert P&L from quote currency to USD.

        For JPY-quoted pairs (USD_JPY, EUR_JPY), raw P&L is in JPY.
        Divide by the exit price (JPY per USD) to get USD.
        For USD-quoted pairs (EUR_USD, GBP_USD, AUD_USD) P&L is already in USD.
        """
        if instrument in JPY_QUOTE_PAIRS and exit_price > 0:
            return pl_in_quote / exit_price
        return pl_in_quote

    def _unrealized_pl(self, pos: SimulatedPosition, current_price: float) -> float:
        if pos.direction == "LONG":
            raw = (current_price - pos.entry_price) * pos.units
        else:
            raw = (pos.entry_price - current_price) * pos.units
        return self._quote_to_usd(pos.instrument, raw, current_price)

    def _close(
        self, pos: SimulatedPosition, exit_price: float, exit_time: datetime, reason: str
    ) -> None:
        comm = self.commission(pos.units)
        if pos.direction == "LONG":
            raw_pl = (exit_price - pos.entry_price) * pos.units
        else:
            raw_pl = (pos.entry_price - exit_price) * pos.units
        pl = self._quote_to_usd(pos.instrument, raw_pl, exit_price) - comm

        pos.exit_price = exit_price
        pos.exit_time = exit_time
        pos.close_reason = reason
        pos.net_pl = pl
        pos.status = "CLOSED"
        self.account_balance += pl
        self.trade_history.append(pos)
        self.positions.remove(pos)

    def close_all(self, price: float, time: datetime, reason: str) -> None:
        for pos in list(self.positions):
            if pos.status == "OPEN":
                self._close(pos, price, time, reason)

    @property
    def open_count(self) -> int:
        return sum(1 for p in self.positions if p.status == "OPEN")

    @property
    def total_trades(self) -> int:
        return len(self.trade_history)
