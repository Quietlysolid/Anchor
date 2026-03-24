"""London benchmark-fix continuation signal engine.

Paper-spec implementation of the validated fix sleeve:
  - universe: USD_JPY, EUR_USD, GBP_USD
  - hypothesis: continue the 1h move into the London fix
  - evaluation window: first hour after the fix only
  - month-end retained as metadata, not a core filter

This engine intentionally does not implement live execution logic yet. The
validated research sleeve is a time-based hold, so paper trading comes first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd
import structlog

from anchor.backtesting.fix_calendar import london_fix_time_utc
from anchor.config import get_settings
from anchor.signals.news_filter import NewsFilter
from anchor.utils.math_utils import get_pip_size
from anchor.utils.time_utils import utcnow

if TYPE_CHECKING:
    from anchor.risk.drawdown_monitor import DrawdownMonitor
    from anchor.risk.spread_monitor import SpreadMonitor

logger = structlog.get_logger(__name__)
settings = get_settings()

FIX_SESSION = "LDN_FIX"
FIX_CONFLUENCE_THRESHOLD = 0.55
FIX_MIN_PRE_MOVE_PIPS = 5.0
FIX_PRE_HOURS = 1
FIX_POST_HOURS = 1
FIX_SCORE_MOVE_CAP_PIPS = 15.0
FIX_SCORE_ATR_CAP = 1.0


@dataclass
class FixSignalResult:
    instrument: str
    direction: str | None = None
    confluence_score: float = 0.0
    move_score: float | None = None
    atr_score: float | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    fix_time_utc: datetime | None = None
    expected_exit_time_utc: datetime | None = None
    pre_move_pips: float | None = None
    atr_pips: float | None = None
    session: str = FIX_SESSION
    suppressed: bool = True
    suppression_reason: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    metadata: dict = field(default_factory=dict)


class FixContinuationEngine:
    def __init__(
        self,
        news_filter: NewsFilter | None = None,
        spread_monitor: "SpreadMonitor" | None = None,
        drawdown_monitor: "DrawdownMonitor" | None = None,
        data_cache: dict[str, dict[str, pd.DataFrame]] | None = None,
    ) -> None:
        self.news_filter = news_filter or NewsFilter()
        self.spread_monitor = spread_monitor
        self.drawdown_monitor = drawdown_monitor
        self.data_cache = data_cache or {}

    async def evaluate(
        self,
        instrument: str,
        dt: datetime | None = None,
    ) -> FixSignalResult:
        dt = dt or utcnow()
        result = FixSignalResult(instrument=instrument, created_at=dt)

        if instrument not in set(settings.fix_instruments):
            result.suppression_reason = f"FIX_INSTRUMENT_EXCLUDED:{instrument}"
            return result

        if dt.weekday() >= 5:
            result.suppression_reason = "FIX_NON_BUSINESS_DAY"
            return result

        fix_time = self._fix_time_for_dt(dt)
        result.fix_time_utc = fix_time.to_pydatetime()
        post_window_end = fix_time + pd.Timedelta(hours=FIX_POST_HOURS)
        if not (fix_time <= pd.Timestamp(dt).tz_convert("UTC") < post_window_end):
            result.suppression_reason = f"FIX_OFF_WINDOW:{dt.hour:02d}UTC"
            return result

        news_ok, news_reason, _ = await self.news_filter.check(instrument, dt)
        if not news_ok:
            result.suppression_reason = news_reason
            return result

        if self.spread_monitor:
            spread_ok, spread_reason = await self.spread_monitor.check(instrument)
            if not spread_ok:
                result.suppression_reason = spread_reason
                return result

        if self.drawdown_monitor:
            dd_ok, dd_reason = self.drawdown_monitor.check()
            if not dd_ok:
                result.suppression_reason = dd_reason
                return result

        df = self._get_data(instrument, "H1")
        if df is None or len(df) < 30:
            result.suppression_reason = "FIX_INSUFFICIENT_DATA"
            return result

        pre_bar_time = fix_time - pd.Timedelta(hours=FIX_PRE_HOURS)
        if pre_bar_time not in df.index:
            result.suppression_reason = "FIX_PRE_BAR_MISSING"
            return result

        pre_bar = df.loc[pre_bar_time]
        pre_move = float(pre_bar["close"] - pre_bar["open"])
        pip = get_pip_size(instrument)
        pre_move_pips = abs(pre_move / pip)
        if pre_move_pips < FIX_MIN_PRE_MOVE_PIPS:
            result.suppression_reason = f"FIX_PREMOVE_TOO_SMALL:{pre_move_pips:.2f}"
            return result

        atr = self._atr_to_bar(df, pre_bar_time)
        atr_pips = abs(atr / pip) if atr is not None and atr > 0 else None

        direction = "LONG" if pre_move > 0 else "SHORT"
        entry_price = float(pre_bar["close"])

        base_stop_pips = max(pre_move_pips, FIX_MIN_PRE_MOVE_PIPS)
        if atr_pips is not None:
            base_stop_pips = max(base_stop_pips, 0.75 * atr_pips)
        stop_distance = base_stop_pips * pip
        take_profit_distance = max(pre_move_pips, FIX_MIN_PRE_MOVE_PIPS) * pip

        if direction == "LONG":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + take_profit_distance
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - take_profit_distance

        move_score = min(1.0, pre_move_pips / FIX_SCORE_MOVE_CAP_PIPS)
        atr_score = None
        if atr_pips is not None and atr_pips > 0:
            atr_score = min(FIX_SCORE_ATR_CAP, pre_move_pips / atr_pips)

        confluence = FIX_CONFLUENCE_THRESHOLD
        confluence += 0.25 * move_score
        confluence += 0.20 * (atr_score if atr_score is not None else 0.5)
        confluence = min(0.99, round(confluence, 4))

        month_end_tag = "ME" if self._is_month_end_business_day(fix_time) else "REG"
        quarter_end = month_end_tag == "ME" and fix_time.month in {3, 6, 9, 12}

        result.direction = direction
        result.confluence_score = confluence
        result.move_score = round(move_score, 4)
        result.atr_score = round(atr_score, 4) if atr_score is not None else None
        result.entry_price = round(entry_price, 5)
        result.stop_loss = round(stop_loss, 5)
        result.take_profit = round(take_profit, 5)
        result.expected_exit_time_utc = (fix_time + pd.Timedelta(hours=FIX_POST_HOURS)).to_pydatetime()
        result.pre_move_pips = round(pre_move_pips, 2)
        result.atr_pips = round(atr_pips, 2) if atr_pips is not None else None
        result.suppressed = False
        result.metadata = {
            "strategy": "FIX_CONTINUATION",
            "fix_time_utc": fix_time.isoformat(),
            "expected_exit_time_utc": (fix_time + pd.Timedelta(hours=FIX_POST_HOURS)).isoformat(),
            "pre_window_start_utc": (fix_time - pd.Timedelta(hours=FIX_PRE_HOURS)).isoformat(),
            "pre_window_end_utc": fix_time.isoformat(),
            "month_end_tag": month_end_tag,
            "quarter_end": quarter_end,
            "pre_move_pips": round(pre_move_pips, 2),
            "atr_pips": round(atr_pips, 2) if atr_pips is not None else None,
            "entry_price": round(entry_price, 5),
            "stop_loss": round(stop_loss, 5),
            "take_profit": round(take_profit, 5),
            "hold_hours": FIX_POST_HOURS,
        }
        return result

    def update_cache(self, instrument: str, timeframe: str, df: pd.DataFrame) -> None:
        self.data_cache.setdefault(timeframe, {})[instrument] = df

    def _get_data(self, instrument: str, timeframe: str) -> pd.DataFrame | None:
        return self.data_cache.get(timeframe, {}).get(instrument)

    def _fix_time_for_dt(self, dt: datetime) -> pd.Timestamp:
        ts = pd.Timestamp(dt)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        day = ts.normalize()
        return london_fix_time_utc(day)

    def _atr_to_bar(self, df: pd.DataFrame, bar_time: pd.Timestamp, period: int = 14) -> float | None:
        sliced = df.loc[:bar_time].tail(period + 1)
        if len(sliced) < period + 1:
            return None
        prev_close = sliced["close"].shift(1)
        true_range = pd.concat(
            [
                sliced["high"] - sliced["low"],
                (sliced["high"] - prev_close).abs(),
                (sliced["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.tail(period).mean()
        if pd.isna(atr):
            return None
        return float(atr)

    def _is_month_end_business_day(self, day: pd.Timestamp) -> bool:
        day = pd.Timestamp(day).tz_convert("UTC").normalize()
        first_next_month = (day.replace(day=1) + pd.Timedelta(days=32)).replace(day=1)
        month_end = (first_next_month - pd.Timedelta(days=1)).normalize()
        while month_end.weekday() >= 5:
            month_end -= pd.Timedelta(days=1)
        return day == month_end
