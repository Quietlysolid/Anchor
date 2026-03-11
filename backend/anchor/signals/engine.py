"""
Confluence Signal Engine.

Orchestrates all signal components into a weighted score.
Only generates a trade signal when all gates pass and score >= threshold.

Flow:
  1. Pre-filters: session, news, spread, drawdown
  2. Direction: determine LONG or SHORT from MTF trend (RSI divergence as booster only)
  3. Multi-timeframe confirmation (4H + Daily)
  4. Component scoring: BB/KC, ADX, S/R, CSI
  5. ML confidence overlay
  6. OOD detection
  7. Final confluence threshold
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog
import ta as ta_lib

from anchor.config import get_settings
from anchor.signals.rsi_divergence    import detect_rsi_divergence
from anchor.signals.bb_kc_squeeze     import detect_squeeze
from anchor.signals.adx_filter        import compute_adx_score
from anchor.signals.support_resistance import compute_sr_score
from anchor.signals.multi_timeframe   import check_mtf_alignment
from anchor.signals.currency_strength import compute_csi, csi_signal_score
from anchor.signals.oanda_sentiment   import sentiment_signal_score, get_cached_score as get_sentiment_score
from anchor.signals.cot_signal        import get_cot_score
from anchor.signals.vix_filter        import get_cached_multiplier as get_vix_multiplier, get_cached_vix
from anchor.data.fred_rates           import get_rate_divergence_score
from anchor.signals.session_filter    import check_session
from anchor.signals.news_filter       import NewsFilter
from anchor.utils.time_utils          import utcnow, get_session_name

if TYPE_CHECKING:
    from anchor.risk.spread_monitor   import SpreadMonitor
    from anchor.risk.drawdown_monitor import DrawdownMonitor
    from anchor.ml.xgb_classifier     import XGBClassifier
    from anchor.ml.ood_detector        import OODDetector
    from anchor.ml.feature_engineer   import FeatureEngineer
    from anchor.regime.hmm_detector   import HMMRegimeDetector

logger = structlog.get_logger(__name__)
settings = get_settings()


@dataclass
class SignalResult:
    instrument:       str
    direction:        str | None     = None
    confluence_score: float          = 0.0
    rsi_score:        float          = 0.0
    bb_kc_score:      float          = 0.0
    adx_score:        float          = 0.0
    sr_score:         float          = 0.0
    mtf_score:        float          = 0.0
    csi_score:        float          = 0.0
    ml_confidence:    float | None   = None
    regime_state:     str | None     = None
    session:          str | None     = None
    suppressed:       bool           = True
    suppression_reason: str | None   = None
    vix_multiplier:   float          = 1.0
    created_at:       datetime       = field(default_factory=utcnow)
    metadata:         dict           = field(default_factory=dict)


# Signal component weights (must sum to 1.0)
# CSI dropped to 0.0: too sparse (USD gets 4 pairs, others 1-2), unreliable.
# COT (institutional positioning, weekly CFTC) at 0.05 — macro filter, fail-open.
# Rate divergence (FRED central bank rate diff) at 0.05 — carry-trade macro filter.
#   Positive rate_diff (base rate > quote) confirms LONG; negative confirms SHORT.
#   Fail-open at 0.5 (neutral) when data unavailable — never blocks alone.
# MTF reduced from 0.10 → 0.05 → 0.03 to make room for macro filters.
# VIX is NOT a confluence component — position-size multiplier only.
# Run make ablation-all after collecting 3 months of live data to auto-optimize weights.
WEIGHTS = {
    "rsi_divergence":    0.24,
    "bb_kc_squeeze":     0.20,
    "adx_filter":        0.15,
    "sr_strength":       0.24,
    "mtf_agreement":     0.04,
    "oanda_sentiment":   0.04,
    "cot_signal":        0.05,
    "rate_divergence":   0.04,
}


class ConfluenceEngine:
    def __init__(
        self,
        news_filter:      NewsFilter      | None = None,
        spread_monitor:   "SpreadMonitor" | None = None,
        drawdown_monitor: "DrawdownMonitor" | None = None,
        ml_classifier:    "XGBClassifier" | None = None,
        ood_detector:     "OODDetector"   | None = None,
        feature_engineer: "FeatureEngineer" | None = None,
        hmm_detector:     "HMMRegimeDetector" | None = None,
        data_cache:       dict[str, dict[str, pd.DataFrame]] | None = None,
        redis_client=None,
        # ── Ablation flags ────────────────────────────────────────────────
        # Set any flag to False to disable that component entirely.
        # Disabled components score 0.0 and their weight is redistributed
        # proportionally across the remaining active components so the
        # confluence score always spans [0, 1] and the threshold is stable.
        ablation_rsi:            bool = True,
        ablation_bb_kc:          bool = True,
        ablation_adx:            bool = True,
        ablation_sr:             bool = True,
        ablation_mtf:            bool = True,
        ablation_sentiment:      bool = True,
        ablation_rate_divergence: bool = True,
        ablation_hmm_gate:       bool = True,   # False → skip VOLATILE block
        ablation_session:        bool = True,   # False → trade all hours
        ablation_ml:             bool = True,   # False → skip ML gate entirely
    ):
        self.news_filter      = news_filter or NewsFilter()
        self.spread_monitor   = spread_monitor
        self.drawdown_monitor = drawdown_monitor
        self.ml_classifier    = ml_classifier
        self.ood_detector     = ood_detector
        self.feature_engineer = feature_engineer
        self.hmm_detector     = hmm_detector
        self.data_cache       = data_cache or {}
        self.redis_client     = redis_client  # optional; used for sentiment + VIX
        # Ablation state
        self._abl_rsi            = ablation_rsi
        self._abl_bb_kc          = ablation_bb_kc
        self._abl_adx            = ablation_adx
        self._abl_sr             = ablation_sr
        self._abl_mtf            = ablation_mtf
        self._abl_sentiment      = ablation_sentiment
        self._abl_rate_divergence = ablation_rate_divergence
        self._abl_hmm_gate       = ablation_hmm_gate
        self._abl_session        = ablation_session
        self._abl_ml             = ablation_ml

    async def evaluate(
        self,
        instrument: str,
        dt: datetime | None = None,
    ) -> SignalResult:
        dt = dt or utcnow()
        result = SignalResult(instrument=instrument, created_at=dt)

        # ── Step 1: Pre-filters (fast rejection) ─────────────────────────
        session_ok, session_reason = check_session(dt, instrument=instrument)
        if not session_ok:
            if self._abl_session:
                result.suppression_reason = session_reason
                result.session = session_reason
                return result
            # ablation_session=False: record off-hours but continue
        else:
            result.session = get_session_name(dt)

        news_ok, news_reason = await self.news_filter.check(instrument, dt)
        if not news_ok:
            result.suppression_reason = news_reason
            return result

        if self.spread_monitor:
            # Fast check: spike vs rolling median (no ATR needed)
            spread_ok, spread_reason = await self.spread_monitor.check(instrument)
            if not spread_ok:
                result.suppression_reason = spread_reason
                return result

        if self.drawdown_monitor:
            drawdown_ok, drawdown_reason = self.drawdown_monitor.check()
            if not drawdown_ok:
                result.suppression_reason = drawdown_reason
                return result

        # ── Step 1b: HMM regime gate ──────────────────────────────────────
        # Block all entries when HMM classifies the market as VOLATILE.
        # The ADX filter handles trending/ranging discrimination downstream;
        # this gate specifically targets the high-vol unpredictable regime.
        if self.hmm_detector and self.hmm_detector.is_ready:
            df_daily_hmm = self._get_data(instrument, "D")
            if df_daily_hmm is not None and len(df_daily_hmm) >= 60:
                hmm_regime, hmm_conf = self.hmm_detector.predict_current(df_daily_hmm)
                result.regime_state = hmm_regime
                result.metadata["hmm_confidence"] = hmm_conf
                if hmm_regime == "VOLATILE" and self._abl_hmm_gate:
                    result.suppression_reason = f"HMM_VOLATILE:{hmm_conf:.3f}"
                    return result

        # ── Step 2: Load data ─────────────────────────────────────────────
        df_1h  = self._get_data(instrument, "H1")
        df_4h  = self._get_data(instrument, "H4")
        df_1d  = self._get_data(instrument, "D")

        if df_1h is None or len(df_1h) < 50:
            result.suppression_reason = "INSUFFICIENT_DATA"
            return result

        # ── Step 2b: ATR-aware spread check ───────────────────────────────
        # Now that we have price data, check spread / ATR ratio to ensure
        # the entry cost is not an excessive fraction of the expected move.
        if self.spread_monitor:
            closes = df_1h["close"].values
            highs  = df_1h["high"].values
            lows   = df_1h["low"].values
            prev_c = np.roll(closes, 1); prev_c[0] = closes[0]
            tr_vals = np.maximum(highs - lows, np.maximum(
                np.abs(highs - prev_c), np.abs(lows - prev_c)
            ))
            _atr_period = 14
            _live_atr = float(np.mean(tr_vals[:_atr_period]))
            _alpha = 1.0 / _atr_period
            for _tv in tr_vals[_atr_period:]:
                _live_atr = _alpha * float(_tv) + (1.0 - _alpha) * _live_atr
            spread_ok2, spread_reason2 = await self.spread_monitor.check(
                instrument, atr=_live_atr
            )
            if not spread_ok2:
                result.suppression_reason = spread_reason2
                return result

        # ── Step 3: Direction determination ──────────────────────────────
        # Direction is always set by MTF trend (trend-following).
        # RSI divergence is used as a confluence booster only when it
        # agrees with the MTF direction — never as a standalone direction driver.
        # (Data shows RSI-divergence-led mean-reversion trades have ~11% WR
        #  vs ~50% WR for MTF-trend-following trades.)
        rsi_raw = detect_rsi_divergence(df_1h)

        # Step 3a: Determine direction from MTF trend
        if df_4h is not None and df_1d is not None:
            long_score, _  = check_mtf_alignment(df_4h, df_1d, "LONG")
            short_score, _ = check_mtf_alignment(df_4h, df_1d, "SHORT")
            if long_score > short_score:
                direction = "LONG"
            elif short_score > long_score:
                direction = "SHORT"
            else:
                # Tied MTF — use RSI level as tiebreaker
                rsi_series = ta_lib.momentum.RSIIndicator(close=df_1h["close"], window=14).rsi()
                rsi_val = float(rsi_series.iloc[-2]) if rsi_series is not None and not rsi_series.isna().all() else 50.0
                direction = "LONG" if rsi_val < 50.0 else "SHORT"
        else:
            result.suppression_reason = "NO_DIRECTION"
            return result

        # Step 3b: RSI divergence as confluence booster (only when it agrees with trend)
        rsi_direction = "LONG" if rsi_raw > 0 else ("SHORT" if rsi_raw < 0 else None)
        if rsi_raw != 0.0 and rsi_direction == direction:
            # Divergence confirms the trend direction — add bonus
            rsi_score = abs(rsi_raw)
            rsi_confirmed = True
        else:
            # No divergence, or divergence contradicts trend → no RSI bonus
            rsi_score = 0.0
            rsi_confirmed = False

        # ── Step 4: Multi-timeframe confirmation ──────────────────────────
        if df_4h is not None and df_1d is not None:
            mtf_score, mtf_reason = check_mtf_alignment(df_4h, df_1d, direction)
        else:
            mtf_score, mtf_reason = 0.5, "NO_HIGHER_TF_DATA"

        if mtf_score == 0.0:
            result.suppression_reason = f"MTF_{mtf_reason}"
            return result

        # ── Step 5: Component scoring ─────────────────────────────────────
        bb_kc_score, squeeze_on = detect_squeeze(df_1h)
        adx_score, adx_regime   = compute_adx_score(df_1h, rsi_confirmed=rsi_confirmed)
        sr_score, sr_level      = compute_sr_score(df_1h, direction=direction)

        # CSI is retained for metadata/logging only (weight = 0.0)
        csi = compute_csi(self.data_cache.get("H1", {}))

        # ── OANDA retail sentiment (contrarian, free from broker) ─────────
        # Reads from Redis cache (populated by scheduler every 5 min).
        # Returns 0.5 (neutral) when Redis is unavailable.
        if self.redis_client is not None:
            raw_sentiment = await get_sentiment_score(self.redis_client, instrument)
        else:
            raw_sentiment = None
        oanda_sentiment_score = sentiment_signal_score(raw_sentiment, direction)

        # ── COT institutional positioning (weekly CFTC, macro trend filter) ─
        # Reads from Redis cache (populated by update_cot_data task, weekly).
        # Returns 0.5 (neutral) when data unavailable — fail-open, never blocks.
        if self.redis_client is not None:
            cot_score = await get_cot_score(self.redis_client, instrument, direction)
        else:
            cot_score = 0.5

        # ── Central bank rate divergence (FRED, daily refresh) ────────────────
        # Carry-trade macro filter: base rate > quote rate → LONG confirmed.
        # Reads from Redis key "fred_rate_diff" (populated by update_fred_rates task).
        # Returns 0.5 (neutral) when data unavailable — fail-open, never blocks.
        if self.redis_client is not None:
            rate_div_score = await get_rate_divergence_score(self.redis_client, instrument, direction)
        else:
            rate_div_score = 0.5

        # ── VIX position-size multiplier ──────────────────────────────────
        # Reads from Redis cache (populated by scheduler every 4 h via FRED).
        # Returns 1.0 (no reduction) when Redis or FRED is unavailable.
        if self.redis_client is not None:
            vix_mult = await get_vix_multiplier(self.redis_client)
            vix_val  = await get_cached_vix(self.redis_client)
        else:
            vix_mult = 1.0
            vix_val  = None

        # Weighted confluence.
        # Ablation: disabled components score 0.0 and their weight is dropped;
        # remaining weights are renormalized so the score still spans [0, 1].
        # When RSI divergence is absent (trend-following path), rsi_score = 0.0
        # and its weight is also excluded before normalization.
        _components = {
            "rsi_divergence":  (rsi_score if rsi_confirmed else 0.0,
                                self._abl_rsi and rsi_confirmed),
            "bb_kc_squeeze":   (bb_kc_score,           self._abl_bb_kc),
            "adx_filter":      (adx_score,              self._abl_adx),
            "sr_strength":     (sr_score,               self._abl_sr),
            "mtf_agreement":   (mtf_score,              self._abl_mtf),
            "oanda_sentiment":  (oanda_sentiment_score,  self._abl_sentiment),
            "cot_signal":       (cot_score,              True),  # always on, fail-open
            "rate_divergence":  (rate_div_score,         self._abl_rate_divergence),
        }
        active_weight_total = sum(
            WEIGHTS[k] for k, (_, active) in _components.items() if active
        )
        if active_weight_total < 1e-9:
            # All components disabled — no signal possible
            result.suppression_reason = "ALL_COMPONENTS_ABLATED"
            return result
        scale = 1.0 / active_weight_total
        confluence = scale * sum(
            WEIGHTS[k] * score
            for k, (score, active) in _components.items()
            if active
        )

        result.rsi_score   = round(rsi_score, 4)
        result.bb_kc_score = round(bb_kc_score, 4)
        result.adx_score   = round(adx_score, 4)
        result.sr_score    = round(sr_score, 4)
        result.mtf_score   = round(mtf_score, 4)
        result.csi_score   = round(oanda_sentiment_score, 4)  # field reused for sentiment
        result.regime_state = adx_regime
        result.vix_multiplier = round(vix_mult, 4)
        result.metadata.update({
            "sr_level":             sr_level,
            "squeeze_on":           squeeze_on,
            "csi":                  csi,          # kept for diagnostics
            "rsi_confirmed":        rsi_confirmed,
            "oanda_sentiment_raw":  raw_sentiment,
            "oanda_sentiment_score": oanda_sentiment_score,
            "cot_score":            cot_score,
            "rate_divergence_score": rate_div_score,
            "vix":                  vix_val,
            "vix_multiplier":       vix_mult,
        })

        # ── Step 6: ML confidence overlay ────────────────────────────────
        if self._abl_ml and self.ml_classifier and self.feature_engineer:
            try:
                features = self.feature_engineer.build(instrument, df_1h, df_4h)
                ml_conf, ml_dir = await asyncio.get_running_loop().run_in_executor(
                    None, self.ml_classifier.predict, features
                )
                result.ml_confidence = round(ml_conf, 4)

                is_ood = False
                if self.ood_detector:
                    is_ood = self.ood_detector.check(features)
                    result.metadata["ood"] = is_ood

                if not is_ood and ml_dir != direction:
                    result.suppression_reason = "ML_DISAGREES"
                    return result

                if not is_ood and ml_conf < settings.min_ml_confidence:
                    result.suppression_reason = "ML_LOW_CONFIDENCE"
                    return result

            except Exception as exc:
                logger.warning(
                    "ml_unavailable_fallback",
                    error=str(exc),
                    instrument=instrument,
                )
                result.metadata["ml_fallback"] = True
                # ML unavailable — continue with base confluence threshold only

        # ── Step 7: Final threshold ───────────────────────────────────────
        if confluence < settings.min_confluence_score:
            result.suppression_reason = f"LOW_CONFLUENCE:{confluence:.3f}"
            return result

        # Signal passes all gates
        result.direction       = direction
        result.confluence_score = round(confluence, 4)
        result.suppressed      = False
        result.suppression_reason = None

        logger.info(
            "signal_generated",
            instrument=instrument,
            direction=direction,
            score=result.confluence_score,
            session=result.session,
            vix=vix_val,
            vix_multiplier=vix_mult,
            sentiment_raw=raw_sentiment,
        )
        return result

    def _get_data(self, instrument: str, timeframe: str) -> pd.DataFrame | None:
        tf_cache = self.data_cache.get(timeframe, {})
        return tf_cache.get(instrument)

    def update_cache(
        self,
        instrument: str,
        timeframe: str,
        df: pd.DataFrame,
    ) -> None:
        if timeframe not in self.data_cache:
            self.data_cache[timeframe] = {}
        self.data_cache[timeframe][instrument] = df
