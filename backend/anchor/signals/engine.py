"""
Confluence Signal Engine.

Orchestrates all signal components into a weighted score.
Only generates a trade signal when all gates pass and score >= threshold.

Flow:
  1. Pre-filters: session, news, spread, drawdown
  2. Direction: determine LONG or SHORT candidate from RSI divergence
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

from anchor.config import get_settings
from anchor.signals.rsi_divergence    import detect_rsi_divergence
from anchor.signals.bb_kc_squeeze     import detect_squeeze
from anchor.signals.adx_filter        import compute_adx_score
from anchor.signals.support_resistance import compute_sr_score
from anchor.signals.multi_timeframe   import check_mtf_alignment
from anchor.signals.currency_strength import compute_csi, csi_signal_score
from anchor.signals.oanda_sentiment   import sentiment_signal_score, get_cached_score as get_sentiment_score
from anchor.signals.vix_filter        import get_cached_multiplier as get_vix_multiplier, get_cached_vix
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
# CSI dropped to 0.0: too sparse with only 5 instruments (USD gets 4 pairs,
# others get 1-2), confirmed unreliable in practice.
# Freed 0.05 weight goes to OANDA retail sentiment (contrarian, free from
# broker, proven edge at extremes).
# VIX is NOT a confluence component — it is a position-size multiplier only,
# applied by the position sizer after signal generation.
WEIGHTS = {
    "rsi_divergence":    0.25,
    "bb_kc_squeeze":     0.20,
    "adx_filter":        0.15,
    "sr_strength":       0.25,
    "mtf_agreement":     0.10,
    "oanda_sentiment":   0.05,
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

    async def evaluate(
        self,
        instrument: str,
        dt: datetime | None = None,
    ) -> SignalResult:
        dt = dt or utcnow()
        result = SignalResult(instrument=instrument, created_at=dt)

        # ── Step 1: Pre-filters (fast rejection) ─────────────────────────
        session_ok, session_reason = check_session(dt)
        if not session_ok:
            result.suppression_reason = session_reason
            result.session = session_reason
            return result

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
                if hmm_regime == "VOLATILE":
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
        # Primary: RSI divergence (highest-quality setups)
        # Fallback: MTF trend alignment (trend-following mode)
        rsi_raw = detect_rsi_divergence(df_1h)
        rsi_score = abs(rsi_raw)  # 0.5 or 1.0; 0.0 if no divergence

        if rsi_raw != 0.0:
            # RSI divergence present — use it for direction
            direction = "LONG" if rsi_raw > 0 else "SHORT"
            rsi_confirmed = True
        else:
            # No RSI divergence — determine direction from MTF trend
            # Try LONG first, then SHORT; pick whichever the trend supports
            if df_4h is not None and df_1d is not None:
                long_score, _  = check_mtf_alignment(df_4h, df_1d, "LONG")
                short_score, _ = check_mtf_alignment(df_4h, df_1d, "SHORT")
                if long_score > short_score:
                    direction = "LONG"
                elif short_score > long_score:
                    direction = "SHORT"
                else:
                    result.suppression_reason = "NO_DIRECTION"
                    return result
            else:
                result.suppression_reason = "NO_DIRECTION"
                return result
            rsi_confirmed = False
            rsi_score = 0.0  # no RSI edge — penalised in confluence

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
        # When RSI divergence is absent (trend-following path), rsi_score = 0.0
        # and its 0.25 weight would cap the maximum achievable confluence at 0.75,
        # making the 0.65 threshold effectively 86.7% of achievable max — nearly
        # impossible to reach. Instead, when RSI is absent we renormalize by
        # distributing the RSI weight proportionally across the remaining components
        # so the score still spans [0, 1] and the threshold is consistently applied.
        if not rsi_confirmed:
            non_rsi_total = 1.0 - WEIGHTS["rsi_divergence"]  # = 0.75
            scale = 1.0 / non_rsi_total  # = 1/0.75 ≈ 1.333
            confluence = scale * (
                WEIGHTS["bb_kc_squeeze"]  * bb_kc_score
                + WEIGHTS["adx_filter"]   * adx_score
                + WEIGHTS["sr_strength"]  * sr_score
                + WEIGHTS["mtf_agreement"] * mtf_score
                + WEIGHTS["oanda_sentiment"] * oanda_sentiment_score
            )
        else:
            confluence = (
                WEIGHTS["rsi_divergence"]    * rsi_score
                + WEIGHTS["bb_kc_squeeze"]   * bb_kc_score
                + WEIGHTS["adx_filter"]      * adx_score
                + WEIGHTS["sr_strength"]     * sr_score
                + WEIGHTS["mtf_agreement"]   * mtf_score
                + WEIGHTS["oanda_sentiment"] * oanda_sentiment_score
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
            "vix":                  vix_val,
            "vix_multiplier":       vix_mult,
        })

        # ── Step 6: ML confidence overlay ────────────────────────────────
        if self.ml_classifier and self.feature_engineer:
            try:
                features = self.feature_engineer.build(instrument, df_1h, df_4h)
                ml_conf, ml_dir = await asyncio.get_event_loop().run_in_executor(
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
                # When ML is unavailable, require a higher confluence to compensate
                # for the missing gate (raise threshold by 10 percentage points)
                if confluence < settings.min_confluence_score + 0.10:
                    result.suppression_reason = f"ML_FALLBACK_LOW_CONFLUENCE:{confluence:.3f}"
                    return result

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
