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
from datetime import timedelta

from anchor.config import get_settings
from anchor.signals.rsi_divergence    import detect_rsi_divergence
from anchor.signals.bb_kc_squeeze     import detect_squeeze
from anchor.signals.adx_filter        import compute_adx_score
from anchor.signals.support_resistance import compute_sr_score
from anchor.signals.multi_timeframe   import check_mtf_alignment
from anchor.signals.currency_strength import compute_csi, csi_signal_score
from anchor.signals.oanda_sentiment   import sentiment_signal_score, get_cached_score as get_sentiment_score
from anchor.signals.cot_signal        import get_cot_score
from anchor.signals.order_book_signal import get_cached_score as get_order_book_score
from anchor.signals.vix_filter        import get_cached_multiplier as get_vix_multiplier, get_cached_vix
from anchor.data.fred_rates           import get_rate_divergence_score
from anchor.data.cme_flow             import get_cached_score as get_cme_flow_score
from anchor.data.fx_options           import get_cached_score as get_fx_options_score
from anchor.data.cross_asset          import get_cached_score as get_cross_asset_score
from anchor.signals.economic_surprise import get_surprise_score
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
    news_multiplier:  float          = 1.0
    created_at:       datetime       = field(default_factory=utcnow)
    metadata:         dict           = field(default_factory=dict)


# Signal component weights (must sum to 1.0)
#
# Design hierarchy (trend-following system):
#   PRIMARY   — MTF alignment (4H+Daily): confirms the macro trend direction
#   SECONDARY — BB/KC squeeze + ADX: confirm momentum and trend strength
#   TERTIARY  — RSI divergence + S/R: entry timing and structural confirmation
#   MACRO     — COT, rate divergence: fail-open filters, never block alone
#   NOISE     — OANDA sentiment: retail is contrarian, low weight
#
# MTF was previously 0.04 (progressively stripped to make room for macro filters).
# Restored to 0.10 — the primary trend confirmation filter must carry real weight.
# RSI 0.24→0.20, S/R 0.24→0.20 (both still primary, slight reduction to fund MTF).
# BB/KC raised 0.20→0.22 (L1 regression confirms it as strongest positive predictor).
#
# NOTE: fit-weights ran on only 77 in-sample trades (< 200 min for reliable L1).
# Re-run `make fit-weights-apply` after accumulating 300+ live trades.
# VIX is NOT a confluence component — position-size multiplier only.
# ── Module-level helpers ──────────────────────────────────────────────────────

def _tsmom_direction(df_daily: pd.DataFrame | None, lookback_days: int = 84) -> str:
    """
    12-week Time-Series Momentum direction filter.
    Moskowitz, Ooi & Pedersen (2012, JFE): sign of 12-week excess return.

    Returns 'LONG', 'SHORT', or 'NEUTRAL' (neutral zone ±0.2%, or insufficient data).
    Fail-open: returns 'NEUTRAL' (allows both directions) when data is unavailable.
    """
    if df_daily is None or len(df_daily) < lookback_days + 2:
        return "NEUTRAL"
    close_now  = float(df_daily["close"].iloc[-1])
    cutoff     = df_daily.index[-1] - pd.Timedelta(days=lookback_days)
    prior      = df_daily[df_daily.index <= cutoff]
    if prior.empty:
        return "NEUTRAL"
    close_past = float(prior["close"].iloc[-1])
    if close_past <= 0:
        return "NEUTRAL"
    ret = (close_now - close_past) / close_past
    if ret >  0.002:
        return "LONG"
    if ret < -0.002:
        return "SHORT"
    return "NEUTRAL"


def _nr4_compression(df_h1: pd.DataFrame | None, dt: "datetime") -> bool:
    """
    NR4 Asian-session compression flag (Crabel 1990).
    True when today's Asian range (00:00–05:00 UTC) is the narrowest
    of the past 4 days — identifies the tightest compression setups.

    Used as metadata/scoring context, not a hard gate.
    Fail-closed: returns False when insufficient data.
    """
    if df_h1 is None or len(df_h1) < 30:
        return False
    today = dt.date() if hasattr(dt, "date") else dt
    ranges: list[float] = []
    for offset in range(4):
        target = today - timedelta(days=offset)
        mask   = (df_h1.index.date == target) & (df_h1.index.hour < 6)
        bars   = df_h1[mask]
        if len(bars) >= 4:
            ranges.append(float(bars["high"].max() - bars["low"].min()))
    if len(ranges) < 4:
        return False
    return ranges[0] <= min(ranges[1:])


WEIGHTS = {
    # ── Core technical components ──────────────────────────────────────────
    "rsi_divergence":    0.16,   # entry timing booster
    "bb_kc_squeeze":     0.17,   # momentum / compression breakout
    "adx_filter":        0.13,   # trend strength gate
    "sr_strength":       0.16,   # structural level confirmation
    "mtf_agreement":     0.10,   # PRIMARY: 4H+Daily trend alignment
    # ── Macro / sentiment ─────────────────────────────────────────────────
    "oanda_sentiment":   0.06,   # contrarian retail book
    "cot_signal":        0.05,   # CFTC non-commercial positioning
    "rate_divergence":   0.02,   # carry + CB velocity (STIR proxy)
    "economic_surprise": 0.04,   # actual vs consensus surprise score
    # ── Institutional-grade signals ───────────────────────────────────────
    "order_book":        0.03,   # OANDA pending-order liquidity magnet
    "cme_flow":          0.03,   # CME futures OI + volume trend
    "fx_options_rr":     0.02,   # FX options risk reversal via ETFs
    "cross_asset":       0.03,   # SPY/GLD risk-on/off → AUD/NZD/JPY/CAD bias
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
        ablation_order_book:     bool = True,   # False → skip OANDA order-book signal
        ablation_cme_flow:       bool = True,   # False → skip CME futures flow signal
        ablation_fx_options:     bool = True,   # False → skip FX options RR signal
        ablation_hmm_gate:       bool = True,   # False → skip VOLATILE block
        ablation_session:        bool = True,   # False → trade all hours
        ablation_ml:             bool = True,   # False → skip ML gate entirely
        ablation_tsmom:          bool = True,   # False → skip TSMOM direction gate
        ablation_cot_gate:       bool = True,   # False → COT conflict never hard-blocks
        ablation_cme_gate:       bool = True,   # False → CME conflict never hard-blocks
        ablation_econ_surprise:  bool = True,   # False → skip economic surprise signal
        ablation_cross_asset:    bool = True,   # False → skip cross-asset risk sentiment
        confluence_threshold:    float | None = None,  # overrides settings.min_confluence_score
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
        self._abl_sentiment       = ablation_sentiment
        self._abl_rate_divergence = ablation_rate_divergence
        self._abl_order_book      = ablation_order_book
        self._abl_cme_flow        = ablation_cme_flow
        self._abl_fx_options      = ablation_fx_options
        self._abl_hmm_gate        = ablation_hmm_gate
        self._abl_session        = ablation_session
        self._abl_ml             = ablation_ml
        self._abl_tsmom          = ablation_tsmom
        self._abl_cot_gate       = ablation_cot_gate
        self._abl_cme_gate       = ablation_cme_gate
        self._abl_econ_surprise  = ablation_econ_surprise
        self._abl_cross_asset    = ablation_cross_asset
        self._confluence_threshold = confluence_threshold

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

        news_ok, news_reason, news_mult = await self.news_filter.check(instrument, dt)
        if not news_ok:
            result.suppression_reason = news_reason
            return result
        result.news_multiplier = news_mult

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

        # ── Step 1c: Session quality gate (Tier 1 + Tier 2a) ─────────────
        # Read from Redis key 'session_quality' (written by presession brief task at 06:30 UTC).
        # On CHOPPY days with high confidence, skip non-top-3 pairs to concentrate edge.
        # Threshold adjustment is applied at the final confluence check below.
        # Fails open: if key is missing, sq_env=MIXED, no pair filtering, no threshold change.
        _sq_env = "MIXED"
        _sq_conf = 0.5
        _sq_threshold_adj = 0.0
        _sq_pair_rankings: list[str] = []
        _sq_size_scale = 1.0
        if self.redis_client is not None:
            try:
                import json as _json_sq
                _sq_raw = await self.redis_client.get("session_quality")
                if _sq_raw:
                    _sq = _json_sq.loads(_sq_raw)
                    _sq_env = _sq.get("environment", "MIXED")
                    _sq_conf = float(_sq.get("confidence", 0.5))
                    _sq_threshold_adj = float(_sq.get("threshold_adjustment", 0.0))
                    _sq_pair_rankings = _sq.get("pair_rankings", [])
                    _sq_size_scale = float(_sq.get("size_scale", 1.0))
            except Exception as _sq_exc:
                logger.warning("session_quality_redis_read_failed", error=str(_sq_exc))

        # Tier 2a: CHOPPY + high confidence → only evaluate the top 3 pairs by macro tailwind
        if _sq_env == "CHOPPY" and _sq_conf >= 0.7 and _sq_pair_rankings:
            top3 = _sq_pair_rankings[:3]
            if instrument not in top3:
                result.suppression_reason = f"CHOPPY_PAIR_FILTER:not_in_top3"
                result.metadata["session_quality_top3"] = top3
                return result

        result.metadata["session_quality_env"] = _sq_env
        result.metadata["session_quality_size_scale"] = _sq_size_scale

        # ── Step 1b: HMM regime gate ──────────────────────────────────────
        # London trend strategy requires a TRENDING regime.
        # - RANGING  → suppress (trend-following in ranging markets is the #1 edge killer)
        # - VOLATILE → suppress (unpredictable high-vol, edges degrade and spreads widen)
        # - TRENDING → allow (this is exactly when we want to trade)
        if self.hmm_detector and self.hmm_detector.is_ready:
            df_daily_hmm = self._get_data(instrument, "D")
            if df_daily_hmm is not None and len(df_daily_hmm) >= 60:
                hmm_regime, hmm_conf = self.hmm_detector.predict_current(df_daily_hmm)
                result.regime_state = hmm_regime
                result.metadata["hmm_confidence"] = hmm_conf
                if hmm_regime in ("VOLATILE", "RANGING") and self._abl_hmm_gate:
                    result.suppression_reason = f"HMM_{hmm_regime}:{hmm_conf:.3f}"
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

        # ── Step 3c: TSMOM macro direction gate (Moskowitz, Ooi & Pedersen 2012) ─
        # 12-week sign-of-return: only trade in the direction of the medium-term trend.
        # Menkhoff et al. (2012) show combining carry + momentum raises Sharpe from
        # ~0.55 to ~1.1. Here we use it as a direction filter, not a sizing signal.
        #
        # Logic:
        #   TSMOM LONG  + MTF direction LONG  → allow  (trend aligned)
        #   TSMOM SHORT + MTF direction SHORT → allow  (trend aligned)
        #   TSMOM LONG  + MTF direction SHORT → suppress (fighting the trend)
        #   TSMOM SHORT + MTF direction LONG  → suppress (fighting the trend)
        #   TSMOM NEUTRAL                     → allow both (±0.2% dead zone)
        #
        # Fail-open: when daily data unavailable, TSMOM returns NEUTRAL → no block.
        tsmom_dir = _tsmom_direction(df_1d)
        result.metadata["tsmom_direction"] = tsmom_dir
        result.metadata["tsmom_aligned"]   = (tsmom_dir == "NEUTRAL" or tsmom_dir == direction)
        if self._abl_tsmom and tsmom_dir != "NEUTRAL" and tsmom_dir != direction:
            result.suppression_reason = f"TSMOM_CONFLICT:{tsmom_dir}_vs_{direction}"
            return result

        # ── Step 3d: NR4 Asian compression flag (Crabel 1990) ────────────
        # Computed from today's Asian session bars (00:00–05:00 UTC) vs prior 3 days.
        # Not a hard gate — logged as metadata and used as a scoring context signal.
        # Backtest finding (ACEB study, 2026-03): NR4 days show +3–5% higher WR
        # on SHORT entries for EUR_USD at London open; logged here for ML feature use.
        nr4 = _nr4_compression(df_1h, dt)
        result.metadata["nr4_compression"] = nr4

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

        # ── OANDA Order Book (liquidity magnet) ───────────────────────────────
        # Reads from Redis key "order_book:{instrument}" (5-min TTL).
        # Identifies where pending orders cluster above vs below current price.
        # Returns (0.5, None) when data unavailable — fail-open.
        if self.redis_client is not None:
            order_book_score, order_book_meta = await get_order_book_score(
                self.redis_client, instrument, direction
            )
        else:
            order_book_score, order_book_meta = 0.5, None

        # ── CME Futures Flow (institutional OI + volume) ──────────────────────
        # Reads from Redis key "cme_flow:{instrument}" (2-hour TTL).
        # Uses yfinance to fetch 6E/6B/6J futures data; inferred OI trend.
        # Returns (0.5, None) when data unavailable — fail-open.
        if self.redis_client is not None:
            cme_flow_score, cme_flow_meta = await get_cme_flow_score(
                self.redis_client, instrument, direction
            )
        else:
            cme_flow_score, cme_flow_meta = 0.5, None

        # ── FX Options Risk Reversal (currency ETF proxy) ─────────────────────
        # Reads from Redis key "fx_options_rr:{instrument}" (4-hour TTL).
        # Computes IV skew from FXE/FXB/FXY listed options (yfinance).
        # Returns (0.5, None) when data unavailable — fail-open.
        if self.redis_client is not None:
            fx_options_score, fx_options_meta = await get_fx_options_score(
                self.redis_client, instrument, direction
            )
        else:
            fx_options_score, fx_options_meta = 0.5, None

        # ── Economic Surprise (actual vs consensus, per-currency) ─────────────
        # Reads per-currency surprise scores from Redis (TTL 1h).
        # Populated by update_economic_surprise scheduler task (every 30 min).
        # Returns 0.5 (neutral) when data unavailable — fail-open.
        if self.redis_client is not None and self._abl_econ_surprise:
            econ_surprise_score = await get_surprise_score(
                self.redis_client, instrument, direction
            )
        else:
            econ_surprise_score = 0.5

        # ── Cross-Asset Risk Sentiment (SPY + GLD regime) ─────────────────────
        # Reads from Redis key "cross_asset_risk" (2-hour TTL).
        # Populated by update_cross_asset scheduler task every 2 hours.
        # Captures risk-on/off flows that drive AUD, NZD, CAD, JPY pairs.
        # Returns (0.5, None) when data unavailable — fail-open.
        if self.redis_client is not None and self._abl_cross_asset:
            cross_asset_score, cross_asset_meta = await get_cross_asset_score(
                self.redis_client, instrument, direction
            )
        else:
            cross_asset_score, cross_asset_meta = 0.5, None

        # ── COT hard gate ─────────────────────────────────────────────────────
        # score < 0.25 means CFTC non-commercial positioning strongly opposes
        # our direction — this is not a haircut situation, it's a conflict.
        # Fail-open: only blocks when Redis is live (score defaults to 0.5).
        if self._abl_cot_gate and cot_score < 0.25:
            result.suppression_reason = f"COT_CONFLICT:{cot_score:.2f}"
            return result

        # ── CME flow hard gate ────────────────────────────────────────────────
        # score < 0.25 means futures OI + volume strongly opposes direction.
        # Only blocks when we have live data (defaults to 0.5 → never blocks).
        if self._abl_cme_gate and cme_flow_score < 0.25:
            result.suppression_reason = f"CME_CONFLICT:{cme_flow_score:.2f}"
            return result

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
            "cot_signal":        (cot_score,              True),   # always on, fail-open
            "rate_divergence":  (rate_div_score,         self._abl_rate_divergence),
            "economic_surprise": (econ_surprise_score,   self._abl_econ_surprise),
            "order_book":       (order_book_score,       self._abl_order_book),
            "cme_flow":         (cme_flow_score,         self._abl_cme_flow),
            "fx_options_rr":    (fx_options_score,       self._abl_fx_options),
            "cross_asset":      (cross_asset_score,      self._abl_cross_asset),
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
            "oanda_sentiment_raw":   raw_sentiment,
            "oanda_sentiment_score": oanda_sentiment_score,
            "cot_score":             cot_score,
            "rate_divergence_score": rate_div_score,
            "order_book_score":      order_book_score,
            "order_book_meta":       order_book_meta,
            "cme_flow_score":        cme_flow_score,
            "cme_flow_meta":         cme_flow_meta,
            "fx_options_score":       fx_options_score,
            "fx_options_meta":        fx_options_meta,
            "econ_surprise_score":    econ_surprise_score,
            "cross_asset_score":      cross_asset_score,
            "cross_asset_meta":       cross_asset_meta,
            "news_multiplier":        result.news_multiplier,
            "vix":                    vix_val,
            "vix_multiplier":        vix_mult,
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

        # ── Step 6b: Macro dissonance penalty (Tier 2c) ──────────────────
        # Redis key macro_dissonance:{pair} is written by the postsession anomaly
        # detector when price action contradicted the macro setup for this pair.
        # Penalty: -0.05 confluence.  Key TTL: 20h (auto-expires after next session).
        # Fail-open: missing key or Redis error → no penalty applied.
        if self.redis_client is not None:
            try:
                _dissonance_val = await self.redis_client.get(f"macro_dissonance:{instrument}")
                if _dissonance_val:
                    confluence = max(0.0, confluence - 0.05)
                    result.metadata["macro_dissonance"] = _dissonance_val
                    logger.debug(
                        "macro_dissonance_penalty",
                        instrument=instrument,
                        confluence_after=round(confluence, 4),
                    )
            except Exception:
                pass  # fail-open

        # ── Step 7: Final threshold ───────────────────────────────────────
        # Tier 1: apply session quality threshold adjustment.
        # TRENDING → lowers threshold (e.g. 0.72 - 0.02 = 0.70) to capture more signals.
        # CHOPPY   → raises threshold (e.g. 0.72 + 0.06 = 0.78) to demand higher conviction.
        # MIXED    → no change (adjustment = 0.0).
        # Falls back to base threshold when session_quality key is missing.
        _base = self._confluence_threshold if self._confluence_threshold is not None else settings.min_confluence_score
        _effective_threshold = _base + _sq_threshold_adj
        result.metadata["effective_threshold"] = round(_effective_threshold, 4)
        if confluence < _effective_threshold:
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
