"""
Mean-Reversion Signal Engine.

STATUS: EXPERIMENTAL — DISABLED FOR PRODUCTION SCHEDULING (as of 2026-03-23).

Validation result: No-Go.
  After fixing a stop-loss direction bug and running a non-leaky HMM regime
  backtest (IS 2018-2022, OOS 2022-2026), MR showed no reliable out-of-sample
  edge. 4 of 5 active pairs had OOS PF < 1.0 under proper regime gating.
  Only USD/CAD showed marginal positive OOS PF but with only 9 OOS trades
  (insufficient statistical evidence).

  The production flag `enable_mr_engine` in config.py is set to False.
  The Celery task in signal_tasks.py checks this flag before calling evaluate()
  and skips the entire block when False.

  To re-enable: set enable_mr_engine = True in config.py AND complete a new
  production-faithful validation showing OOS PF > 1.15 on 2+ pairs with 50+
  trades each. See mr_regime_backtest.py for the validated backtest template.

The SL-direction fix below (stop_loss must be on the correct side of entry)
was applied 2026-03-23 and must be preserved regardless of strategy status.

Runs in parallel with ConfluenceEngine. Only active when HMM regime is RANGING
and ADX < 25. Fades price to the Bollinger Band mean (middle band).

Strategy rationale (Ernest Chan, "Algorithmic Trading" ch.2-3):
  - In ranging markets, price reverts to mean after touching the outer BB (2σ).
  - The edge is highest when:
      1. ADX confirms no trend (< 25)
      2. RSI is at an extreme (overbought/oversold) — momentum exhaustion
      3. The entry candle shows rejection (wick-to-body ratio > 1.5) — pin bar
      4. Price is NOT at a key S/R level that could accelerate a breakout
  - SL = beyond the BB outer band + 0.5×ATR buffer (handles wicks)
  - TP = BB middle band (the mean) → actual R:R varies ~1.5:1 to 3:1 depending
    on how deep into the band the entry is

Session: London only (07:00–12:00 UTC) — same as trend engine.
  Ranging setups in London typically resolve within 2-4 hours before NY open
  injects directional momentum, so holding time is naturally bounded.

Confluence weights (sum to 1.0):
  bb_touch     0.35 — how far price has penetrated the outer BB (deeper = stronger)
  rsi_extreme  0.30 — RSI distance from 50 toward extreme (65/35 required to fire)
  rejection    0.20 — wick-to-body ratio on the entry candle (pin bar strength)
  adx_confirm  0.15 — ADX score (lower ADX = higher score for mean reversion)

Threshold: 0.60 (slightly lower than trend 0.65 — ranging setups are structurally
cleaner because the entry point is at an extreme, so a lower bar is justified).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import structlog
import ta as ta_lib

from anchor.config import get_settings
from anchor.signals.adx_filter     import compute_adx_score
from anchor.signals.session_filter import check_session
from anchor.signals.news_filter    import NewsFilter
from anchor.utils.time_utils       import utcnow, get_session_name

if TYPE_CHECKING:
    from anchor.risk.spread_monitor   import SpreadMonitor
    from anchor.risk.drawdown_monitor import DrawdownMonitor
    from anchor.regime.hmm_detector   import HMMRegimeDetector

logger = structlog.get_logger(__name__)
settings = get_settings()

MR_CONFLUENCE_THRESHOLD = 0.60
MR_RSI_OVERBOUGHT      = 65.0   # above this → SHORT setup
MR_RSI_OVERSOLD        = 35.0   # below this → LONG setup
MR_ADX_MAX             = 25.0   # hard gate — no mean reversion above this
MR_ATR_SL_BUFFER       = 0.5    # ATR buffer beyond BB outer band for SL
MR_ATR_TP_RATIO        = 1.0    # TP = mid-band; ratio used to sanity-check R:R
MR_MIN_RR              = 1.2    # skip if (TP distance / SL distance) < 1.2

MR_WEIGHTS = {
    "bb_touch":    0.35,
    "rsi_extreme": 0.30,
    "rejection":   0.20,
    "adx_confirm": 0.15,
}


@dataclass
class MRSignalResult:
    instrument:       str
    direction:        str | None   = None
    confluence_score: float        = 0.0
    bb_touch_score:   float        = 0.0
    rsi_score:        float        = 0.0
    rejection_score:  float        = 0.0
    adx_score:        float        = 0.0
    entry_price:      float | None = None
    stop_loss:        float | None = None
    take_profit:      float | None = None
    atr:              float | None = None
    regime_state:     str | None   = None
    session:          str | None   = None
    suppressed:       bool         = True
    suppression_reason: str | None = None
    created_at:       datetime     = field(default_factory=utcnow)
    metadata:         dict         = field(default_factory=dict)


class MeanReversionEngine:
    """
    Bollinger Band fade strategy for RANGING regime.

    Instantiate once per worker, inject data_cache and optional dependencies,
    call evaluate() per instrument per scan cycle.
    """

    def __init__(
        self,
        news_filter:      NewsFilter        | None = None,
        spread_monitor:   "SpreadMonitor"   | None = None,
        drawdown_monitor: "DrawdownMonitor" | None = None,
        hmm_detector:     "HMMRegimeDetector" | None = None,
        data_cache:       dict[str, dict[str, pd.DataFrame]] | None = None,
    ):
        self.news_filter      = news_filter or NewsFilter()
        self.spread_monitor   = spread_monitor
        self.drawdown_monitor = drawdown_monitor
        self.hmm_detector     = hmm_detector
        self.data_cache       = data_cache or {}

    async def evaluate(
        self,
        instrument: str,
        dt: datetime | None = None,
    ) -> MRSignalResult:
        dt = dt or utcnow()
        result = MRSignalResult(instrument=instrument, created_at=dt)

        # ── Gate 1: Session ───────────────────────────────────────────────
        session_ok, session_reason = check_session(dt, instrument=instrument)
        if not session_ok:
            result.suppression_reason = session_reason
            result.session = session_reason
            return result
        result.session = get_session_name(dt)

        # ── Gate 2: News ──────────────────────────────────────────────────
        news_ok, news_reason, _news_mult = await self.news_filter.check(instrument, dt)
        if not news_ok:
            result.suppression_reason = news_reason
            return result

        # ── Gate 3: Spread ────────────────────────────────────────────────
        if self.spread_monitor:
            spread_ok, spread_reason = await self.spread_monitor.check(instrument)
            if not spread_ok:
                result.suppression_reason = spread_reason
                return result

        # ── Gate 4: Drawdown circuit breaker ──────────────────────────────
        if self.drawdown_monitor:
            dd_ok, dd_reason = self.drawdown_monitor.check()
            if not dd_ok:
                result.suppression_reason = dd_reason
                return result

        # ── Gate 5: HMM regime must be RANGING ────────────────────────────
        # Mean reversion only makes sense when there is no sustained trend.
        # TRENDING → ConfluenceEngine handles it.
        # VOLATILE → neither engine should trade.
        if self.hmm_detector and self.hmm_detector.is_ready:
            df_daily = self._get_data(instrument, "D")
            if df_daily is not None and len(df_daily) >= 60:
                regime, conf = self.hmm_detector.predict_current(df_daily)
                result.regime_state = regime
                result.metadata["hmm_confidence"] = conf
                if regime != "RANGING":
                    result.suppression_reason = f"HMM_NOT_RANGING:{regime}"
                    return result
            else:
                # No daily data → can't confirm regime → skip
                result.suppression_reason = "HMM_NO_DATA"
                return result
        else:
            # No HMM loaded → fall back to ADX-only gate below
            result.regime_state = "UNKNOWN"

        # ── Load H1 data ──────────────────────────────────────────────────
        df = self._get_data(instrument, "H1")
        if df is None or len(df) < 50:
            result.suppression_reason = "INSUFFICIENT_DATA"
            return result

        # ── Gate 6: ADX hard gate (< 25) ──────────────────────────────────
        adx_score, adx_regime = compute_adx_score(df, rsi_confirmed=True)
        # adx_score is mean-reversion suitability: high when ADX is LOW.
        # adx_score < 0.5 means ADX is in TRANSITIONING or TRENDING territory.
        # adx_regime check provides a human-readable guard.
        if adx_regime not in ("RANGING", "UNKNOWN"):
            result.suppression_reason = f"ADX_NOT_RANGING:{adx_regime}"
            return result

        # ── Compute indicators ────────────────────────────────────────────
        # ATR (14)
        prev_close = df["close"].shift(1)
        true_range = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"]  - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = float(true_range.rolling(14).mean().iloc[-1])
        if pd.isna(atr) or atr <= 0:
            result.suppression_reason = "ATR_UNAVAILABLE"
            return result

        # Bollinger Bands (20, 2σ)
        bb = ta_lib.volatility.BollingerBands(close=df["close"], window=20, window_dev=2.0)
        bb_upper = bb.bollinger_hband()
        bb_lower = bb.bollinger_lband()
        bb_mid   = bb.bollinger_mavg()

        if bb_upper is None or bb_upper.isna().all():
            result.suppression_reason = "BB_UNAVAILABLE"
            return result

        upper = float(bb_upper.iloc[-1])
        lower = float(bb_lower.iloc[-1])
        mid   = float(bb_mid.iloc[-1])
        close = float(df["close"].iloc[-1])
        high  = float(df["high"].iloc[-1])
        low   = float(df["low"].iloc[-1])
        open_ = float(df["open"].iloc[-1])

        # ── Gate 7: Price must be at or outside the outer band ────────────
        at_upper = close >= (upper - atr * 0.1)   # within 0.1 ATR of upper band
        at_lower = close <= (lower + atr * 0.1)   # within 0.1 ATR of lower band
        if not at_upper and not at_lower:
            result.suppression_reason = "PRICE_NOT_AT_BAND"
            return result

        direction = "SHORT" if at_upper else "LONG"

        # ── Score 1: BB touch depth ────────────────────────────────────────
        # How far beyond the band has price reached?
        # Deeper penetration → stronger reversion signal.
        if direction == "SHORT":
            penetration = max(0.0, close - upper)
        else:
            penetration = max(0.0, lower - close)

        # Score 0.5 at band touch, 1.0 at 1×ATR beyond band
        bb_touch_score = min(1.0, 0.5 + (penetration / atr) * 0.5)

        # ── Score 2: RSI extreme ──────────────────────────────────────────
        rsi_series = ta_lib.momentum.RSIIndicator(close=df["close"], window=14).rsi()
        if rsi_series is None or rsi_series.isna().all():
            result.suppression_reason = "RSI_UNAVAILABLE"
            return result
        rsi_val = float(rsi_series.iloc[-1])

        if direction == "SHORT":
            if rsi_val < MR_RSI_OVERBOUGHT:
                result.suppression_reason = f"RSI_NOT_EXTREME:{rsi_val:.1f}"
                return result
            # Score: 0.5 at 65, 1.0 at 80+
            rsi_score = min(1.0, 0.5 + (rsi_val - MR_RSI_OVERBOUGHT) / 30.0 * 0.5)
        else:
            if rsi_val > MR_RSI_OVERSOLD:
                result.suppression_reason = f"RSI_NOT_EXTREME:{rsi_val:.1f}"
                return result
            # Score: 0.5 at 35, 1.0 at 20-
            rsi_score = min(1.0, 0.5 + (MR_RSI_OVERSOLD - rsi_val) / 30.0 * 0.5)

        # ── Score 3: Rejection candle (pin bar) ───────────────────────────
        # For SHORT: long upper wick (wick > body, wick > 1.5× body)
        # For LONG:  long lower wick
        body = abs(close - open_)
        if direction == "SHORT":
            wick = high - max(close, open_)   # upper wick
        else:
            wick = min(close, open_) - low    # lower wick

        if body < 1e-8:
            # Doji — no clear rejection direction, treat as moderate
            rejection_score = 0.5
        else:
            ratio = wick / body
            # ratio >= 1.5 → strong pin bar (score 1.0)
            # ratio 0.5-1.5 → moderate (score scales)
            # ratio < 0.5   → no rejection (score 0.0)
            rejection_score = min(1.0, max(0.0, (ratio - 0.5) / 1.0))

        # ── Weighted confluence ────────────────────────────────────────────
        confluence = (
            MR_WEIGHTS["bb_touch"]    * bb_touch_score +
            MR_WEIGHTS["rsi_extreme"] * rsi_score      +
            MR_WEIGHTS["rejection"]   * rejection_score +
            MR_WEIGHTS["adx_confirm"] * adx_score
        )

        result.bb_touch_score  = round(bb_touch_score, 4)
        result.rsi_score       = round(rsi_score, 4)
        result.rejection_score = round(rejection_score, 4)
        result.adx_score       = round(adx_score, 4)
        result.atr             = round(atr, 6)
        result.metadata.update({
            "rsi_val":    round(rsi_val, 2),
            "bb_upper":   round(upper, 5),
            "bb_lower":   round(lower, 5),
            "bb_mid":     round(mid, 5),
            "adx_regime": adx_regime,
        })

        if confluence < MR_CONFLUENCE_THRESHOLD:
            result.suppression_reason = f"LOW_CONFLUENCE:{confluence:.3f}"
            return result

        # ── Compute SL / TP and check minimum R:R ─────────────────────────
        # Entry: current close (market order — price is already at the band extreme)
        # SL: beyond the outer band + 0.5×ATR buffer (absorbs wicks)
        # TP: middle band (the statistical mean)
        entry = close
        if direction == "SHORT":
            stop_loss   = round(upper + MR_ATR_SL_BUFFER * atr, 5)
            take_profit = round(mid, 5)
        else:
            stop_loss   = round(lower - MR_ATR_SL_BUFFER * atr, 5)
            take_profit = round(mid, 5)

        # Sanity check: SL must be on the correct side of entry.
        # When price drops more than 0.5×ATR below the lower BB, the formula
        # (lower - 0.5×ATR) can land above the current close, producing a LONG
        # trade where SL > entry. abs() in sl_dist hides this and creates phantom
        # high-R:R wins in the backtest.
        if direction == "LONG" and stop_loss >= entry:
            result.suppression_reason = f"SL_WRONG_SIDE:sl={stop_loss:.5f}_entry={entry:.5f}"
            return result
        if direction == "SHORT" and stop_loss <= entry:
            result.suppression_reason = f"SL_WRONG_SIDE:sl={stop_loss:.5f}_entry={entry:.5f}"
            return result

        sl_dist = abs(entry - stop_loss)
        tp_dist = abs(entry - take_profit)

        if sl_dist <= 0 or tp_dist / sl_dist < MR_MIN_RR:
            result.suppression_reason = (
                f"POOR_RR:{tp_dist/sl_dist:.2f}" if sl_dist > 0 else "ZERO_SL"
            )
            return result

        # Signal passes all gates
        result.direction       = direction
        result.confluence_score = round(confluence, 4)
        result.entry_price     = round(entry, 5)
        result.stop_loss       = stop_loss
        result.take_profit     = take_profit
        result.suppressed      = False
        result.suppression_reason = None

        logger.info(
            "mr_signal_generated",
            instrument=instrument,
            direction=direction,
            score=result.confluence_score,
            entry=entry,
            sl=stop_loss,
            tp=take_profit,
            rr=round(tp_dist / sl_dist, 2),
            rsi=round(rsi_val, 1),
            regime=result.regime_state,
            session=result.session,
        )
        return result

    def _get_data(self, instrument: str, timeframe: str) -> pd.DataFrame | None:
        return self.data_cache.get(timeframe, {}).get(instrument)

    def update_cache(self, instrument: str, timeframe: str, df: pd.DataFrame) -> None:
        if timeframe not in self.data_cache:
            self.data_cache[timeframe] = {}
        self.data_cache[timeframe][instrument] = df
