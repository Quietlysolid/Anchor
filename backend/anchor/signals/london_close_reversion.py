"""
London Close Reversal (LCR) Engine — New York session (17:00–20:00 UTC).

Mathematical basis:
  During London (07:00–17:00 UTC), institutional traders build directional
  positions. At London close, they liquidate intraday positions, creating
  counter-trend pressure. Price reverts toward the session's statistical
  midpoint — the level where supply/demand was balanced during the day.

  This is the most documented intraday mean-reversion pattern in FX:
    - Documented in: Harris & Pisedtasalasai (2006), Breedon & Ranaldo (2013)
    - EUR/USD: average 30-50% retracement of London range within 2 hours of close
    - Works because London close causes coordinated position reduction, not trend

Strategy:
  1. Calculate London session range from H1 bars (07:00–16:00 UTC same day).
     L_high = max(bar highs),  L_low = min(bar lows)
     L_range = L_high - L_low,  L_mid = (L_high + L_low) / 2

  2. Position = (close - L_low) / L_range  → [0, 1]
     - Position > 0.75 → price in top quarter → SHORT (reverting down toward mid)
     - Position < 0.25 → price in bottom quarter → LONG (reverting up toward mid)

  3. TP  = L_mid (natural mean-reversion target — the day's balance point)
     SL  = L_high + ATR_buffer (SHORT) or L_low - ATR_buffer (LONG)
     ATR buffer = 0.5 × 14-bar ATR (absorbs wick excursions beyond London extreme)

Confluence weights:
  range_position  0.40  — how extreme the price position is in London range
  rsi_extreme     0.30  — RSI exhaustion confirms overextension at extreme
  rejection       0.20  — pin bar / wick rejection on current bar (momentum shift)
  range_quality   0.10  — London range size vs ATR (quality gate)

Threshold: 0.55 (lower than trend engine — LCR entry point is already at an
extreme, making it structurally cleaner. Entry confirmation via RSI + rejection
provides sufficient quality gate.)

Allowed instruments: derived from config.instruments (currently EUR_USD, GBP_USD,
  NZD_USD, USD_CAD, EUR_JPY, AUD_USD). Previously included USD_JPY, USD_CHF,
  GBP_JPY — all removed after 8-year backtest (DD disqualifiers or weak edge).
  LCR_INSTRUMENTS is now `set(settings.instruments)` — edit config.py to change.

Session window: 17:00–19:59 UTC (bars opening at 17, 18, 19)
  - 17:00 = first bar after London close → strongest initial reversal signal
  - 20:00 onward = thin NY liquidity → signal quality degrades
  - Friday after 18:00 UTC → suppress (weekend gap risk)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import structlog
import ta as ta_lib

from anchor.config import get_settings
from anchor.signals.news_filter import NewsFilter
from anchor.utils.time_utils import utcnow
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anchor.risk.spread_monitor import SpreadMonitor
    from anchor.risk.drawdown_monitor import DrawdownMonitor
    from anchor.regime.hmm_detector import HMMRegimeDetector

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Constants ─────────────────────────────────────────────────────────────────

LCR_INSTRUMENTS = set(settings.instruments)  # derived from config — single source of truth

LCR_CONFLUENCE_THRESHOLD = 0.55

LCR_WEIGHTS = {
    "range_position": 0.40,
    "rsi_extreme":    0.30,
    "rejection":      0.20,
    "range_quality":  0.10,
}

LCR_RSI_OVERBOUGHT = 60.0   # above → confirms SHORT reversion signal
LCR_RSI_OVERSOLD   = 40.0   # below → confirms LONG reversion signal
LCR_ATR_SL_BUFFER  = 0.5    # ATR multiplier for SL beyond London extreme
LCR_MIN_RR         = 1.2    # minimum R:R ratio (tp_dist / sl_dist)
LCR_MIN_RANGE_ATR  = 0.4    # London range must be ≥ 0.4×ATR (filter dead days)

# London session bar hours (UTC): bars that OPEN during London session
_LONDON_HOURS = set(range(7, 17))   # 07:00–16:00 inclusive
# NY window: bars that OPEN at 17, 18, 19 UTC
_NY_LCR_HOURS = {17, 18, 19}


@dataclass
class LCRSignalResult:
    instrument:        str
    direction:         str | None   = None
    confluence_score:  float        = 0.0
    range_pos_score:   float        = 0.0
    rsi_score:         float        = 0.0
    rejection_score:   float        = 0.0
    range_qual_score:  float        = 0.0
    entry_price:       float | None = None
    stop_loss:         float | None = None
    take_profit:       float | None = None
    london_high:       float | None = None
    london_low:        float | None = None
    london_mid:        float | None = None
    atr:               float | None = None
    session:           str          = "NY_LCR"
    suppressed:        bool         = True
    suppression_reason: str | None  = None
    created_at:        datetime     = field(default_factory=utcnow)
    metadata:          dict         = field(default_factory=dict)


class LondonCloseReversionEngine:
    """
    London Close Reversal signal generator for the NY session.

    Instantiate once per worker. Inject data_cache (same dict as ConfluenceEngine)
    and optional risk monitors. Call evaluate() per instrument per H1 bar close.

    Only fires during 17:00–19:59 UTC. All other times return suppressed=True.
    """

    def __init__(
        self,
        news_filter:      NewsFilter      | None = None,
        spread_monitor:   "SpreadMonitor" | None = None,
        drawdown_monitor: "DrawdownMonitor" | None = None,
        data_cache:       dict[str, dict[str, pd.DataFrame]] | None = None,
        hmm_detector:     "HMMRegimeDetector" | None = None,
    ) -> None:
        self.news_filter      = news_filter or NewsFilter()
        self.spread_monitor   = spread_monitor
        self.drawdown_monitor = drawdown_monitor
        self.data_cache       = data_cache or {}
        self.hmm_detector     = hmm_detector

    async def evaluate(
        self,
        instrument: str,
        dt: datetime | None = None,
    ) -> LCRSignalResult:
        dt = dt or utcnow()
        result = LCRSignalResult(instrument=instrument, created_at=dt)

        # ── Gate 1: Instrument allowlist ──────────────────────────────────
        if instrument not in LCR_INSTRUMENTS:
            result.suppression_reason = f"LCR_INSTRUMENT_EXCLUDED:{instrument}"
            return result

        # ── Gate 2: NY session window (17:00–19:59 UTC) ───────────────────
        hour = dt.hour
        if hour not in _NY_LCR_HOURS:
            result.suppression_reason = f"LCR_OFF_WINDOW:{hour:02d}UTC"
            return result

        # Friday after 18:00 UTC — weekend gap risk
        if dt.weekday() == 4 and hour >= 18:
            result.suppression_reason = "LCR_FRIDAY_THIN"
            return result

        # ── Gate 3: News filter ───────────────────────────────────────────
        news_ok, news_reason, _news_mult = await self.news_filter.check(instrument, dt)
        if not news_ok:
            result.suppression_reason = news_reason
            return result

        # ── Gate 4: Spread / drawdown ─────────────────────────────────────
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

        # ── Gate 4b: HMM regime gate ──────────────────────────────────────
        # LCR is a mean-reversion strategy — it relies on price reverting to the
        # London midpoint after institutional liquidation. This works best in
        # RANGING or mildly TRENDING markets where liquidation pressure dominates.
        #
        # In a strong TRENDING regime, NY session often continues the London
        # trend rather than reversing it — exactly when LCR fails. We raise the
        # confluence threshold from 0.55 → 0.65 to demand stronger confirmation.
        # VOLATILE regime: block outright (spreads blow out, fills are unreliable).
        _lcr_threshold = LCR_CONFLUENCE_THRESHOLD  # default 0.55
        if self.hmm_detector and self.hmm_detector.is_ready:
            _df_d = self.data_cache.get("D", {}).get(instrument)
            if _df_d is not None and len(_df_d) >= 60:
                _regime, _regime_conf = self.hmm_detector.predict_current(_df_d)
                result.regime_state = _regime
                result.metadata["hmm_regime"]     = _regime
                result.metadata["hmm_confidence"] = round(_regime_conf, 4)
                if _regime == "VOLATILE":
                    result.suppression_reason = f"LCR_HMM_VOLATILE:{_regime_conf:.3f}"
                    return result
                if _regime == "TRENDING":
                    # Raise bar — only highest-quality LCR setups survive a trending day
                    _lcr_threshold = 0.65

        # ── Gate 5: Load H1 data ──────────────────────────────────────────
        df = self._get_data(instrument, "H1")
        if df is None or len(df) < 25:
            result.suppression_reason = "LCR_INSUFFICIENT_DATA"
            return result

        # ── Gate 6: Extract London session bars for today ─────────────────
        # The H1 dataframe has bars indexed by open time (UTC).
        # At dt=17:00 UTC, bars up to 16:00 UTC are in the window.
        idx = df.index
        if hasattr(idx, "tzinfo") or (hasattr(idx, "tz") and idx.tz is not None):
            dt_date = dt.astimezone(timezone.utc).date()
        else:
            dt_date = dt.date()

        # Try to extract date and hour from the index
        try:
            if hasattr(idx, "date"):
                london_mask = (
                    pd.Series(idx.date, index=idx) == dt_date
                ) & pd.Series(idx.hour, index=idx).isin(_LONDON_HOURS)
                london_bars = df.loc[london_mask.values]
            else:
                # Index is integer or other — fall back to 'time' column if present
                if "time" in df.columns:
                    t_series = pd.to_datetime(df["time"], utc=True)
                    london_mask = (t_series.dt.date == dt_date) & (
                        t_series.dt.hour.isin(_LONDON_HOURS)
                    )
                    london_bars = df[london_mask.values]
                else:
                    result.suppression_reason = "LCR_NO_TIME_INDEX"
                    return result
        except Exception as exc:
            result.suppression_reason = f"LCR_TIME_PARSE_ERROR:{exc}"
            return result

        if len(london_bars) < 5:
            # Need at least 5 London bars to compute a meaningful range
            result.suppression_reason = f"LCR_INSUFFICIENT_LONDON_BARS:{len(london_bars)}"
            return result

        # ── Step 1: London session range ──────────────────────────────────
        london_high = float(london_bars["high"].max())
        london_low  = float(london_bars["low"].min())
        london_mid  = (london_high + london_low) / 2.0
        london_range = london_high - london_low

        if london_range <= 0:
            result.suppression_reason = "LCR_ZERO_RANGE"
            return result

        _dp = 3 if instrument.endswith("JPY") or instrument.startswith("JPY") else 5
        result.london_high = round(london_high, _dp)
        result.london_low  = round(london_low, _dp)
        result.london_mid  = round(london_mid, _dp)

        # ── ATR (14-bar Wilder's) from H1 window ──────────────────────────
        closes = df["close"].values
        highs  = df["high"].values
        lows   = df["low"].values
        prev_c = np.roll(closes, 1)
        prev_c[0] = closes[0]
        tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)))

        _p = 14
        atr = float(np.mean(tr[:_p]))
        _alpha = 1.0 / _p
        for _tv in tr[_p:]:
            atr = _alpha * float(_tv) + (1.0 - _alpha) * atr

        if atr <= 0 or np.isnan(atr):
            result.suppression_reason = "LCR_ATR_INVALID"
            return result

        result.atr = round(atr, 6)

        # ── Gate 7: Range quality — London range must be meaningful ───────
        # A dead day (london_range < 0.4×ATR) means no displacement to revert
        range_quality = london_range / atr  # ratio
        if range_quality < LCR_MIN_RANGE_ATR:
            result.suppression_reason = f"LCR_RANGE_TOO_SMALL:{range_quality:.2f}xATR"
            return result

        # ── Step 2: Current price position in London range ────────────────
        current_close = float(df["close"].iloc[-1])
        current_high  = float(df["high"].iloc[-1])
        current_low   = float(df["low"].iloc[-1])
        current_open  = float(df["open"].iloc[-1])

        position = (current_close - london_low) / london_range  # [0, 1]

        if position > 0.75:
            direction = "SHORT"
        elif position < 0.25:
            direction = "LONG"
        else:
            result.suppression_reason = f"LCR_PRICE_NOT_AT_EXTREME:{position:.2f}"
            return result

        # ── Score 1: Range position extremeness ───────────────────────────
        # 0.75 → 0.50 score, 1.0 → 1.0 score (for SHORT)
        # 0.25 → 0.50 score, 0.0 → 1.0 score (for LONG)
        if direction == "SHORT":
            range_pos_score = min(1.0, 0.5 + (position - 0.75) / 0.25 * 0.5)
        else:
            range_pos_score = min(1.0, 0.5 + (0.25 - position) / 0.25 * 0.5)

        # ── Score 2: RSI extreme (momentum exhaustion) ────────────────────
        rsi_series = ta_lib.momentum.RSIIndicator(close=df["close"], window=14).rsi()
        if rsi_series is None or rsi_series.isna().all():
            rsi_score = 0.5  # neutral — don't suppress, just don't boost
        else:
            rsi_val = float(rsi_series.iloc[-1])
            if direction == "SHORT":
                if rsi_val >= LCR_RSI_OVERBOUGHT:
                    # Score: 0.5 at RSI=60, 1.0 at RSI=75+
                    rsi_score = min(1.0, 0.5 + (rsi_val - LCR_RSI_OVERBOUGHT) / 30.0 * 0.5)
                else:
                    rsi_score = max(0.0, rsi_val / LCR_RSI_OVERBOUGHT * 0.5)
            else:  # LONG
                if rsi_val <= LCR_RSI_OVERSOLD:
                    # Score: 0.5 at RSI=40, 1.0 at RSI=25-
                    rsi_score = min(1.0, 0.5 + (LCR_RSI_OVERSOLD - rsi_val) / 30.0 * 0.5)
                else:
                    rsi_score = max(0.0, (100 - rsi_val) / (100 - LCR_RSI_OVERSOLD) * 0.5)
            result.metadata["rsi_val"] = round(rsi_val, 2)

        # ── Score 3: Rejection candle (confirms momentum shift) ───────────
        body = abs(current_close - current_open)
        if direction == "SHORT":
            wick = current_high - max(current_close, current_open)  # upper wick
        else:
            wick = min(current_close, current_open) - current_low   # lower wick

        if body < 1e-8:
            rejection_score = 0.5   # doji — neutral
        else:
            ratio = wick / body
            # ratio ≥ 1.5 → strong pin bar (1.0), ratio < 0.5 → no rejection (0.0)
            rejection_score = min(1.0, max(0.0, (ratio - 0.5) / 1.0))

        # ── Score 4: Range quality score ──────────────────────────────────
        # 0.4×ATR → 0.5, 1.0×ATR → 1.0 (diminishing returns above 1.5×ATR)
        range_qual_score = min(1.0, 0.5 + (range_quality - LCR_MIN_RANGE_ATR) / 1.2 * 0.5)

        # ── Weighted confluence ────────────────────────────────────────────
        confluence = (
            LCR_WEIGHTS["range_position"] * range_pos_score +
            LCR_WEIGHTS["rsi_extreme"]    * rsi_score       +
            LCR_WEIGHTS["rejection"]      * rejection_score  +
            LCR_WEIGHTS["range_quality"]  * range_qual_score
        )

        result.range_pos_score  = round(range_pos_score, 4)
        result.rsi_score        = round(rsi_score, 4)
        result.rejection_score  = round(rejection_score, 4)
        result.range_qual_score = round(range_qual_score, 4)
        result.metadata.update({
            "position_in_range":  round(position, 4),
            "london_range_atr":   round(range_quality, 3),
            "london_range_price": round(london_range, 5),
        })

        if confluence < _lcr_threshold:
            result.suppression_reason = f"LCR_LOW_CONFLUENCE:{confluence:.3f}<{_lcr_threshold:.2f}"
            return result

        # ── SL / TP and R:R check ──────────────────────────────────────────
        # JPY pairs use 3 decimal places; all others use 5
        _price_dp = 3 if instrument.endswith("JPY") or instrument.startswith("JPY") else 5
        entry = current_close
        if direction == "SHORT":
            stop_loss   = round(london_high + LCR_ATR_SL_BUFFER * atr, _price_dp)
            take_profit = round(london_mid, _price_dp)
        else:
            stop_loss   = round(london_low - LCR_ATR_SL_BUFFER * atr, _price_dp)
            take_profit = round(london_mid, _price_dp)

        sl_dist = abs(entry - stop_loss)
        tp_dist = abs(entry - take_profit)

        if sl_dist <= 0:
            result.suppression_reason = "LCR_ZERO_SL"
            return result

        rr = tp_dist / sl_dist
        if rr < LCR_MIN_RR:
            result.suppression_reason = f"LCR_POOR_RR:{rr:.2f}"
            return result

        # Signal passes all gates
        result.direction       = direction
        result.confluence_score = round(confluence, 4)
        result.entry_price     = round(entry, _price_dp)
        result.stop_loss       = stop_loss
        result.take_profit     = take_profit
        result.suppressed      = False
        result.suppression_reason = None

        logger.info(
            "lcr_signal_generated",
            instrument=instrument,
            direction=direction,
            score=result.confluence_score,
            entry=entry,
            sl=stop_loss,
            tp=take_profit,
            rr=round(rr, 2),
            position=round(position, 3),
            london_range_atr=round(range_quality, 2),
            session="NY_LCR",
        )
        return result

    def _get_data(self, instrument: str, timeframe: str) -> pd.DataFrame | None:
        return self.data_cache.get(timeframe, {}).get(instrument)

    def update_cache(self, instrument: str, timeframe: str, df: pd.DataFrame) -> None:
        if timeframe not in self.data_cache:
            self.data_cache[timeframe] = {}
        self.data_cache[timeframe][instrument] = df
