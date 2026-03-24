"""Signal scan task: London trend, M15, fix continuation, mean-reversion, and LCR evaluation + execution."""
from __future__ import annotations

from datetime import timedelta

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import (
    _run_async,
    _drawdown_monitor,
    _daily_limiter,
    _spread_monitor,
    _alerts,
)

logger = structlog.get_logger(__name__)


def _regime_size_scale(regime_state: str | None, strategy: str) -> float:
    """Return a position-size multiplier based on market regime and strategy type.

    strategy='trend'  — London / M15 trend-following: optimal in TRENDING, penalised in RANGING/VOLATILE.
    strategy='revert' — LCR / MR mean-reversion: optimal in RANGING, penalised in TRENDING/VOLATILE.

    Returns 1.0 when regime is None / UNKNOWN so the gate fails open (no size change).
    """
    if not regime_state or regime_state == "UNKNOWN":
        logger.warning("regime_size_scale_fallback", regime_state=regime_state, strategy=strategy)
        return 1.0
    if strategy == "trend":
        return {"TRENDING": 1.0, "RANGING": 0.80, "VOLATILE": 0.60}.get(regime_state, 1.0)
    if strategy == "revert":
        return {"RANGING": 1.0, "TRENDING": 0.80, "VOLATILE": 0.60}.get(regime_state, 1.0)
    return 1.0


@celery_app.task(name="anchor.scheduler.jobs.run_signal_scan", bind=True, max_retries=2)
def run_signal_scan(self):
    """Evaluate confluence signals for all instruments on every H1 candle close.

    When a signal passes all filters (suppressed=False), immediately size and
    submit an order to OANDA via the OrderManager execution pipeline.
    """
    async def _inner():
        import json
        import pandas as pd
        from anchor.config import settings
        from anchor.database.engine import init_db
        from anchor.database.repositories.market_data import MarketDataRepository
        from anchor.database.repositories.orders import OrderRepository
        from anchor.database.repositories.positions import PositionRepository
        from anchor.database.repositories.signals import SignalRepository
        from anchor.database.models import Signal as SignalModel
        from anchor.signals.engine import ConfluenceEngine
        from anchor.signals.fix_continuation import FixContinuationEngine, FIX_SESSION
        from anchor.signals.macro_state_overlay import get_macro_state_overlay
        from anchor.signals.mean_reversion_engine import MeanReversionEngine
        from anchor.signals.nfp_continuation import (
            NFP_SESSION,
            NfpContinuationEngine,
            fetch_recent_nfp_bundle,
        )
        from anchor.signals.news_filter import NewsFilter
        from anchor.signals.positioning_overlay import get_cot_positioning_overlay
        from anchor.database.repositories import EconomicCalendarRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.execution.order_manager import OrderManager
        from anchor.execution.order_types import OrderRequest, Direction, OrderType
        from anchor.risk.position_sizer import PositionSizer
        from anchor.risk.correlation import CorrelationManager
        from anchor.utils.time_utils import utcnow
        from anchor.ml.xgb_classifier import XGBDirectionClassifier
        from anchor.ml.feature_engineer import FeatureEngineer
        from anchor.ml.ood_detector import OODDetector
        import redis
        import redis.asyncio as aioredis

        await init_db()
        import anchor.database.engine as _db_engine
        now = utcnow()

        # Sync Redis client for FeatureEngineer (called in executor thread, not async)
        sync_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        active_instruments = settings.instruments
        trend_instruments = settings.trend_instruments
        fix_instruments = settings.fix_instruments
        nfp_instruments = settings.nfp_instruments
        scan_instruments = sorted(set(active_instruments) | set(fix_instruments) | set(nfp_instruments) | set(trend_instruments))

        broker = BrokerClient()
        sizer = PositionSizer()
        drawdown_monitor = _drawdown_monitor   # persistent singleton
        daily_limiter = _daily_limiter         # persistent singleton
        correlation_mgr = CorrelationManager()

        # Get live account state once for the whole scan
        account = await broker.get_account_summary()
        balance = float(account.get("balance", 0))
        equity  = float(account.get("NAV", balance))

        # Bootstrap durable risk state from DB on the first scan after a worker restart
        # so drawdown / monthly circuit breakers don't silently reset.
        if drawdown_monitor._peak_equity is None or drawdown_monitor._month_start_equity is None:
            async with _db_engine.AsyncSessionFactory() as _bootstrap_session:
                if drawdown_monitor._peak_equity is None:
                    await drawdown_monitor.bootstrap_peak_equity(_bootstrap_session)
                if drawdown_monitor._month_start_equity is None:
                    await drawdown_monitor.bootstrap_month_state(_bootstrap_session, current_equity=equity)

        drawdown_monitor.update(equity)

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

        # Wire Redis into spread monitor so it can read cross-process spread data
        # (stream runs in the FastAPI process; worker has a separate in-memory instance)
        _spread_monitor.set_redis(redis_client)

        # Read session quality and rolling score for logging only — NOT applied to sizing.
        # AI size_scale and rolling_score_scale are unvalidated signals removed from execution.
        # Values are logged so they can be correlated against trade outcomes at 200 trades.
        try:
            import json as _json_sq
            from anchor.intelligence.session_quality import _REDIS_KEY as _SQ_KEY
            _sq_raw = await redis_client.get(_SQ_KEY)
            if _sq_raw:
                _sq_data = _json_sq.loads(_sq_raw)
                logger.debug(
                    "scan_session_quality_logged_only",
                    environment=_sq_data.get("environment"),
                    size_scale=_sq_data.get("size_scale"),
                    threshold_adj=_sq_data.get("threshold_adjustment"),
                )
        except Exception as _sq_err:
            logger.warning("scan_session_quality_read_failed", error=str(_sq_err))

        try:
            _raw_avg = await redis_client.get("rolling_score_avg")
            if _raw_avg is not None:
                logger.debug("scan_rolling_score_logged_only", avg=round(float(_raw_avg), 2))
        except Exception as _rs_err:
            logger.warning("scan_rolling_score_read_failed", error=str(_rs_err))

        # Fixed at 1.0 — no AI size scaling until validated against live trade data
        _session_size_scale = 1.0
        _rolling_score_scale = 1.0

        def to_df(rows):
            if not rows:
                df = pd.DataFrame(columns=["open", "high", "low", "close"])
                df.index.name = "time"
                return df
            return pd.DataFrame([{
                "time": r.time, "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close),
            } for r in rows]).set_index("time")

        # Load ML classifiers from disk (written by retrain_models task).
        # Falls back gracefully — ConfluenceEngine raises threshold +10% when absent.
        from pathlib import Path as _Path
        # Pass sync Redis to FeatureEngineer so COT features are populated at prediction time
        feature_engineer = FeatureEngineer(redis_client=sync_redis)
        _classifiers: dict = {}
        _ood_detectors: dict = {}
        for _inst in trend_instruments:
            _clf = XGBDirectionClassifier()
            _model_path = _Path("/app/models") / f"{_inst}_xgb.pkl"
            if _clf.load(_model_path):
                _classifiers[_inst] = _clf
                _ood = OODDetector()
                _ood_path = _Path("/app/models") / f"{_inst}_ood.pkl"
                _ood.load(_ood_path)  # gracefully no-ops if file missing
                _ood_detectors[_inst] = _ood
            else:
                logger.warning("ml_model_missing", instrument=_inst, path=str(_model_path))

        news_filter = NewsFilter(calendar_repo=None)

        # Build correlation matrix + open positions in a short-lived session
        async with _db_engine.AsyncSessionFactory() as session:
            market_repo   = MarketDataRepository(session)
            position_repo = PositionRepository(session)
            order_repo_pre = OrderRepository(session)

            daily_closes = {}
            for inst in trend_instruments:
                rows = await market_repo.get_latest_n_candles(inst, "D", 250)
                if len(rows) >= 2:
                    daily_closes[inst] = pd.Series(
                        [float(r.close) for r in rows],
                        index=[r.time for r in rows],
                    )
            await correlation_mgr.update(daily_closes)
            open_positions = await position_repo.get_open()
            # Also treat pending/submitted limit orders as "already committed" so we
            # don't stack duplicate orders every 5 min while waiting for a fill.
            pending_orders = await order_repo_pre.get_pending()
            pending_instruments = {o.instrument for o in pending_orders}

        # Load HMM regime detector from disk (written by run_regime_detection task).
        # Fails gracefully — engine skips the HMM gate when detector is not ready.
        from anchor.regime.hmm_detector import HMMRegimeDetector
        from pathlib import Path as _HMMPath

        # Load per-instrument HMM models (written by run_regime_detection).
        # Falls back to the shared "hmm_latest.pkl" if the per-instrument file is missing,
        # and to no HMM at all (engine skips the gate) if neither file exists.
        _hmm_detectors: dict = {}
        for _inst in trend_instruments:
            _per_inst_path  = _HMMPath("/app/models") / f"hmm_{_inst}.pkl"
            _fallback_path  = _HMMPath("/app/models/hmm_latest.pkl")
            _det = HMMRegimeDetector()
            if _det.load(_per_inst_path):
                _hmm_detectors[_inst] = _det
            else:
                _det2 = HMMRegimeDetector()
                if _det2.load(_fallback_path):
                    _hmm_detectors[_inst] = _det2
                    logger.debug("hmm_using_fallback_model", instrument=_inst)
                else:
                    logger.warning("hmm_model_missing", instrument=_inst)

        # Shared engine instance; ML/HMM/OOD detectors swapped per-instrument below
        engine = ConfluenceEngine(
            news_filter=news_filter,
            spread_monitor=_spread_monitor,
            feature_engineer=feature_engineer,
            ood_detector=None,
            hmm_detector=None,  # set per-instrument in the loop below
            redis_client=redis_client,  # enables OANDA sentiment + VIX lookups
        )

        # Mean-reversion engine: runs in parallel, only fires in RANGING regime
        mr_engine = MeanReversionEngine(
            news_filter=news_filter,
            spread_monitor=_spread_monitor,
            drawdown_monitor=drawdown_monitor,
            hmm_detector=None,  # set per-instrument in the loop below
        )

        # London benchmark-fix continuation engine: paper-only sleeve for now.
        fix_engine = FixContinuationEngine(
            news_filter=news_filter,
            spread_monitor=_spread_monitor,
            drawdown_monitor=drawdown_monitor,
        )
        nfp_engine = NfpContinuationEngine()
        recent_nfp_bundle = None
        if settings.enable_nfp_engine:
            try:
                async with _db_engine.AsyncSessionFactory() as _nfp_bundle_session:
                    recent_nfp_bundle = await fetch_recent_nfp_bundle(_nfp_bundle_session, now)
            except Exception as _nfp_bundle_exc:
                logger.warning("nfp_bundle_fetch_failed", error=str(_nfp_bundle_exc))

        # London Close Reversal engine: NY session (17:00–19:59 UTC), EUR/GBP/JPY only
        from anchor.signals.london_close_reversion import LondonCloseReversionEngine, LCR_INSTRUMENTS
        lcr_engine = LondonCloseReversionEngine(
            news_filter=news_filter,
            spread_monitor=_spread_monitor,
            drawdown_monitor=drawdown_monitor,
            hmm_detector=None,  # injected per-instrument inside the scan loop below
        )

        # Guard: fire drawdown narration at most once per unique trigger event.
        # Redis key dd_narrated:{date}:{type} gates narration across scan cycles.
        _dd_narration_fired_this_scan: bool = False

        # Evaluate + persist each instrument in its own isolated session
        # so one DB error doesn't poison the others
        for instrument in scan_instruments:
            try:
                if settings.enable_trend_engine and instrument in trend_instruments:
                    async with _db_engine.AsyncSessionFactory() as session:
                        market_repo   = MarketDataRepository(session)
                        order_repo    = OrderRepository(session)
                        order_manager = OrderManager(order_repo, broker, redis=redis_client)
    
                        # Wire live calendar repo so the news filter actually queries the DB
                        news_filter.calendar_repo = EconomicCalendarRepository(session)
    
                        h1 = await market_repo.get_latest_n_candles(instrument, "H1", 200)
                        h4 = await market_repo.get_latest_n_candles(instrument, "H4", 100)
                        d1 = await market_repo.get_latest_n_candles(instrument, "D", 250)  # 200+ needed for SMA(200) MTF check
    
                        if len(h1) < 50:
                            logger.warning("signal_scan_insufficient_data", instrument=instrument)
                            continue
    
                        engine.update_cache(instrument, "H1", to_df(h1))
                        engine.update_cache(instrument, "H4", to_df(h4))
                        engine.update_cache(instrument, "D",  to_df(d1))
    
                        # Inject instrument-specific classifier and HMM (None → engine falls back gracefully)
                        engine.hmm_detector   = _hmm_detectors.get(instrument)
                        engine.ml_classifier  = _classifiers.get(instrument)
                        if not engine.ml_classifier:
                            engine.feature_engineer = None
                            engine.ood_detector = None
                        else:
                            engine.feature_engineer = feature_engineer
                            engine.ood_detector = _ood_detectors.get(instrument)
    
                        result = await engine.evaluate(instrument, dt=now)

                        if result.direction:
                            _macro_overlay = await get_macro_state_overlay(
                                redis_client, instrument, result.direction
                            )
                            result.metadata["macro_state_overlay"] = _macro_overlay.to_metadata()
                            if _macro_overlay.veto and not result.suppressed:
                                result.suppressed = True
                                result.suppression_reason = f"MACRO_STATE_VETO:{_macro_overlay.regime}"

                            _pos_overlay = await get_cot_positioning_overlay(
                                redis_client, instrument, result.direction
                            )
                            result.metadata["positioning_overlay"] = _pos_overlay.to_metadata()
                            if _pos_overlay.veto and not result.suppressed:
                                result.suppressed = True
                                result.suppression_reason = f"COT_POSITIONING_VETO:{_pos_overlay.regime}"
                        else:
                            _macro_overlay = None
                            _pos_overlay = None
    
                        # Log component breakdown for every in-session evaluation so we
                        # can diagnose exactly which gate is blocking each instrument.
                        # Off-session suppression (no session name set) stays at DEBUG.
                        _in_session = result.session in ("LONDON", "OVERLAP")
                        (logger.info if _in_session else logger.debug)(
                            "signal_evaluated",
                            instrument=instrument,
                            suppressed=result.suppressed,
                            reason=result.suppression_reason,
                            confluence=result.confluence_score,
                            rsi=result.rsi_score,
                            bb_kc=result.bb_kc_score,
                            adx=result.adx_score,
                            sr=result.sr_score,
                            mtf=result.mtf_score,
                            sentiment=result.csi_score,
                            ml_confidence=result.ml_confidence,
                            regime=result.regime_state,
                            session=result.session,
                            vix_mult=result.vix_multiplier,
                        )
    
                        # Persist every evaluation for audit trail
                        row = SignalModel(
                            instrument=instrument,
                            timeframe="H1",
                            direction=result.direction or "LONG",
                            confluence_score=result.confluence_score,
                            rsi_score=result.rsi_score,
                            bb_kc_score=result.bb_kc_score,
                            adx_score=result.adx_score,
                            sr_score=result.sr_score,
                            mtf_score=result.mtf_score,
                            csi_score=result.csi_score,
                            ml_confidence=result.ml_confidence,
                            regime_state=result.regime_state,
                            session=result.session,
                            suppressed=result.suppressed,
                            suppression_reason=result.suppression_reason,
                            signal_metadata=result.metadata or {},
                        )
                        session.add(row)
                        await session.flush()  # get row.id assigned
    
                        # Broadcast signal to dashboard via Redis → WebSocket fanout
                        try:
                            meta = result.metadata or {}
                            await redis_client.publish("signals", json.dumps({
                                "channel": "signals",
                                "data": {
                                    "id":                    str(row.id),
                                    "created_at":            row.created_at.isoformat() if row.created_at else None,
                                    "instrument":            instrument,
                                    "timeframe":             "H1",
                                    "direction":             result.direction,
                                    "confluence_score":      float(result.confluence_score),
                                    "rsi_score":             float(result.rsi_score)       if result.rsi_score       is not None else None,
                                    "bb_kc_score":           float(result.bb_kc_score)     if result.bb_kc_score     is not None else None,
                                    "adx_score":             float(result.adx_score)       if result.adx_score       is not None else None,
                                    "sr_score":              float(result.sr_score)        if result.sr_score        is not None else None,
                                    "mtf_score":             float(result.mtf_score)       if result.mtf_score       is not None else None,
                                    "csi_score":             float(result.csi_score)       if result.csi_score       is not None else None,
                                    "cot_score":             float(meta["cot_score"])             if meta.get("cot_score")             is not None else None,
                                    "macro_state_overlay":   meta.get("macro_state_overlay"),
                                    "positioning_overlay":   meta.get("positioning_overlay"),
                                    "rate_divergence_score": float(meta["rate_divergence_score"]) if meta.get("rate_divergence_score") is not None else None,
                                    "order_book_score":      float(meta["order_book_score"])      if meta.get("order_book_score")      is not None else None,
                                    "cme_flow_score":        float(meta["cme_flow_score"])        if meta.get("cme_flow_score")        is not None else None,
                                    "fx_options_score":      float(meta["fx_options_score"])      if meta.get("fx_options_score")      is not None else None,
                                    "econ_surprise_score":   float(meta["econ_surprise_score"])   if meta.get("econ_surprise_score")   is not None else None,
                                    "news_multiplier":       float(meta["news_multiplier"])       if meta.get("news_multiplier")       is not None else None,
                                    "ml_confidence":         float(result.ml_confidence)   if result.ml_confidence   is not None else None,
                                    "regime_state":          result.regime_state,
                                    "session":               result.session,
                                    "suppressed":            result.suppressed,
                                    "suppression_reason":    result.suppression_reason,
                                },
                            }))
                        except Exception as _ws_exc:
                            logger.warning("signal_ws_publish_failed", error=str(_ws_exc))
    
                        if result.suppressed:
                            await session.commit()
                            continue
    
    
                        if settings.trend_paper_only:
                            logger.info("trend_signal_paper_only", instrument=instrument, direction=result.direction)
                            await session.commit()
                            continue
    
                        # ── Execution gate ────────────────────────────────────────
    
                        # 1. Drawdown circuit breaker
                        dd_ok, dd_reason = drawdown_monitor.check()
                        if not dd_ok:
                            logger.warning("trade_blocked_drawdown", instrument=instrument, reason=dd_reason)
                            await _alerts.send_critical(f"Drawdown circuit breaker triggered\n{dd_reason}\nBalance: ${balance:.2f}")
    
                            # Narrate the drawdown once per trigger event (gated by Redis TTL)
                            if not _dd_narration_fired_this_scan:
                                _dd_narration_fired_this_scan = True
                                try:
                                    from datetime import date as _date
                                    _trigger_type = "HALT" if "HALT" in (dd_reason or "") else "REDUCE"
                                    _gate_key = f"dd_narrated:{_date.today().isoformat()}:{_trigger_type}"
                                    if not await redis_client.exists(_gate_key):
                                        await redis_client.set(_gate_key, "1", ex=4 * 3_600)
    
                                        # Fetch recent trades for context
                                        from sqlalchemy import text as _sqlt
                                        from anchor.database.engine import AsyncSessionFactory as _ASL
                                        _recent_trades: list[dict] = []
                                        try:
                                            async with _ASL() as _s:
                                                _rows = (await _s.execute(_sqlt("""
                                                    SELECT instrument, direction, net_pl,
                                                           session_at_entry, regime_at_entry,
                                                           close_reason, closed_at
                                                    FROM trades
                                                    WHERE closed_at IS NOT NULL
                                                    ORDER BY closed_at DESC LIMIT 20
                                                """))).fetchall()
                                                _recent_trades = [dict(r._mapping) for r in _rows]
                                        except Exception as _exc:
                                            logger.debug("dd_narration_trades_query_failed", error=str(_exc))
    
                                        # Macro snapshot from Redis
                                        import json as _j
                                        _macro: dict = {}
                                        for _rk, _rl in [("vix_data", "vix"), ("cross_asset_risk", "cross_asset"), ("fred_rate_diff", "rate_differentials")]:
                                            try:
                                                _rv = await redis_client.get(_rk)
                                                if _rv:
                                                    _macro[_rl] = _j.loads(_rv)
                                            except Exception as _exc:
                                                logger.debug("dd_narration_redis_read_failed", key=_rk, error=str(_exc))
    
                                        from anchor.intelligence.trade_intelligence import narrate_drawdown as _narrate_dd
                                        _narration, _ = await _narrate_dd(
                                            _trigger_type,
                                            drawdown_monitor.current_drawdown,
                                            _recent_trades,
                                            _macro,
                                        )
                                        if _narration:
                                            await _alerts.send_warning(
                                                f"🧠 Drawdown Analysis ({_trigger_type} — {drawdown_monitor.current_drawdown:.1%})\n\n{_narration}"
                                            )
                                except Exception as _dd_narr_exc:
                                    logger.warning("drawdown_narration_failed", error=str(_dd_narr_exc))
    
                            await session.commit()
                            continue
    
                        # 2. Daily loss limit
                        if daily_limiter.is_halted(balance):
                            logger.warning("trade_blocked_daily_limit", instrument=instrument)
                            await _alerts.send_critical(f"Daily loss limit hit — trading halted for today\nBalance: ${balance:.2f}")
                            await session.commit()
                            continue
    
                        # 3. Already have an open position in this instrument?
                        already_open = any(p.instrument == instrument for p in open_positions) or instrument in pending_instruments
                        if already_open:
                            logger.info("trade_skipped_already_open", instrument=instrument)
                            await session.commit()
                            continue
    
                        # 4. Correlation check
                        corr_ok, corr_reason = correlation_mgr.check_new_position(
                            instrument, result.direction, open_positions
                        )
                        if not corr_ok:
                            logger.warning("trade_blocked_correlation", instrument=instrument, reason=corr_reason)
                            await session.commit()
                            continue
    
                        # 5. Compute true ATR-based stop loss & take profit.
                        # True ATR = max(H-L, |H-prev_C|, |L-prev_C|) — accounts for overnight gaps.
                        h1_df = to_df(h1)
                        prev_close = h1_df["close"].shift(1)
                        true_range = pd.concat([
                            h1_df["high"] - h1_df["low"],
                            (h1_df["high"] - prev_close).abs(),
                            (h1_df["low"]  - prev_close).abs(),
                        ], axis=1).max(axis=1)
                        atr = true_range.rolling(14).mean().iloc[-1]
    
                        # ATR volatility filter: skip dead markets (no movement = noise) and
                        # news spikes (ATR 5× normal = stop blown through unpredictably).
                        atr_series = true_range.rolling(14).mean()
                        atr_pct_20 = atr_series.rolling(30).quantile(0.20).iloc[-1]
                        atr_pct_95 = atr_series.rolling(30).quantile(0.95).iloc[-1]
                        if not (pd.isna(atr_pct_20) or pd.isna(atr_pct_95)):
                            if atr < atr_pct_20:
                                logger.debug("trade_blocked_dead_market", instrument=instrument, atr=atr)
                                await session.commit()
                                continue
                            if atr > atr_pct_95:
                                logger.debug("trade_blocked_news_spike", instrument=instrument, atr=atr)
                                await session.commit()
                                continue
    
                        # Pullback entry: limit order at 50% of the signal candle's body.
                        # For LONG:  limit below close (wait for a dip into support).
                        # For SHORT: limit above close (wait for a pop into resistance).
                        # This improves effective R:R from 2:1 → ~2.5:1 without widening the stop.
                        # Doji guard: if candle body < 0.2×ATR (doji), use 0.3×ATR as minimum
                        # pullback so we don't accidentally submit a market-price limit order.
                        last_candle = h1_df.iloc[-1]
                        candle_body = abs(float(last_candle["close"]) - float(last_candle["open"]))
                        pullback    = max(candle_body * 0.5, atr * 0.3) if candle_body < atr * 0.2 else candle_body * 0.5
    
                        close_price = float(h1_df["close"].iloc[-1])
                        if result.direction == "LONG":
                            entry       = round(close_price - pullback, 5)
                            stop_loss   = round(entry - 1.5 * atr, 5)
                            take_profit = round(entry + 2.0 * atr, 5)
                        else:
                            entry       = round(close_price + pullback, 5)
                            stop_loss   = round(entry + 1.5 * atr, 5)
                            take_profit = round(entry - 2.0 * atr, 5)
    
                        # Limit order expires after 4 hours — prevents stale fills
                        # in the next session under completely different conditions.
                        gtd_time = now + timedelta(hours=4)
    
                        # 6. Size the position
                        units = sizer.compute(
                            account_balance=balance,
                            instrument=instrument,
                            entry_price=entry,
                            stop_loss=stop_loss,
                            risk_pct_override=settings.trend_risk_pct,
                            kelly_fraction=float(result.ml_confidence) if result.ml_confidence else None,
                            drawdown_scale=drawdown_monitor.scale_factor,
                            vix_scale=result.vix_multiplier,
                            news_scale=result.news_multiplier,
                            session_scale=_session_size_scale,
                            rolling_score_scale=_rolling_score_scale,
                            regime_scale=_regime_size_scale(result.regime_state, "trend"),
                            positioning_scale=(
                                float((result.metadata or {}).get("positioning_overlay", {}).get("multiplier", 1.0))
                                if result.metadata else 1.0
                            ) * (
                                float((result.metadata or {}).get("macro_state_overlay", {}).get("multiplier", 1.0))
                                if result.metadata else 1.0
                            ),
                        )
    
                        # 7. Submit limit order (GTD — expires in 4 hours if not filled)
                        direction = Direction.LONG if result.direction == "LONG" else Direction.SHORT
                        order_request = OrderRequest(
                            instrument=instrument,
                            direction=direction,
                            units=units,
                            order_type=OrderType.LIMIT,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            limit_price=entry,
                            gtd_time=gtd_time,
                            signal_id=row.id,
                        )
    
                        order_id = await order_manager.submit(order_request)
                        pending_instruments.add(instrument)  # keep snapshot current for later engines this scan
                        logger.info(
                            "limit_order_submitted",
                            instrument=instrument,
                            direction=result.direction,
                            units=units,
                            limit_price=entry,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            confluence=result.confluence_score,
                            expires=gtd_time.isoformat(),
                            order_id=str(order_id),
                        )
                        await _alerts.send_info(
                            f"Order placed: {result.direction} {instrument}\n"
                            f"Entry: {entry}  SL: {stop_loss}  TP: {take_profit}\n"
                            f"Units: {units}  Confluence: {result.confluence_score:.2f}\n"
                            f"Expires: {gtd_time.strftime('%H:%M UTC')}"
                        )
                        await session.commit()
                else:
                    logger.debug("trend_engine_disabled_skipping", instrument=instrument)


            except Exception as exc:
                logger.error("signal_scan_instrument_failed", instrument=instrument, error=str(exc))
                await _alerts.send_warning(f"Signal scan failed for {instrument}\n{exc}")

            # ── Mean-reversion scan (separate try block — trend failure must not block MR) ──
            # MR is DISABLED (validated No-Go 2026-03-23). The block is kept intact so
            # re-enabling is a one-line config change (enable_mr_engine = True in config.py).
            # Do not re-enable without a new production-faithful backtest showing OOS PF > 1.15.
            try:
                if settings.enable_mr_engine and instrument in trend_instruments:
                    async with _db_engine.AsyncSessionFactory() as mr_session:
                        mr_market_repo = MarketDataRepository(mr_session)
                        mr_order_repo    = OrderRepository(mr_session)
                        mr_order_manager = OrderManager(mr_order_repo, broker, redis=redis_client)
                        news_filter.calendar_repo = EconomicCalendarRepository(mr_session)
    
                        # Populate MR cache here — independent of whether the trend block ran.
                        mr_h1 = await mr_market_repo.get_latest_n_candles(instrument, "H1", 200)
                        mr_d1 = await mr_market_repo.get_latest_n_candles(instrument, "D", 250)
                        mr_engine.update_cache(instrument, "H1", to_df(mr_h1))
                        mr_engine.update_cache(instrument, "D",  to_df(mr_d1))
                        mr_engine.hmm_detector = _hmm_detectors.get(instrument)
    
                        mr_result = await mr_engine.evaluate(instrument, dt=now)
    
                        logger.debug(
                            "mr_signal_evaluated",
                            instrument=instrument,
                            suppressed=mr_result.suppressed,
                            reason=mr_result.suppression_reason,
                            confluence=mr_result.confluence_score,
                            regime=mr_result.regime_state,
                            session=mr_result.session,
                        )
    
                        if mr_result.suppressed:
                            continue
    
                        if settings.mr_paper_only:
                            logger.info("mr_signal_paper_only", instrument=instrument, direction=mr_result.direction)
                            await mr_session.commit()
                            continue
    
                        # Execution gates (same as trend engine)
                        _mr_dd_ok, _mr_dd_reason = drawdown_monitor.check()
                        if not _mr_dd_ok:
                            logger.warning("trade_blocked_drawdown", instrument=instrument, reason=_mr_dd_reason)
                            await _alerts.send_critical(f"Drawdown circuit breaker triggered\n{_mr_dd_reason}\nBalance: ${balance:.2f}")
                            await mr_session.commit()
                            continue
                        if daily_limiter.is_halted(balance):
                            logger.warning("trade_blocked_daily_limit", instrument=instrument)
                            await _alerts.send_critical(f"Daily loss limit hit — trading halted for today\nBalance: ${balance:.2f}")
                            await mr_session.commit()
                            continue
                        already_open = any(p.instrument == instrument for p in open_positions) or instrument in pending_instruments
                        if already_open:
                            continue
                        corr_ok, _ = correlation_mgr.check_new_position(
                            instrument, mr_result.direction, open_positions
                        )
                        if not corr_ok:
                            continue
    
                        # R:R already validated inside mr_engine.evaluate()
                        mr_units = sizer.compute(
                            account_balance=balance,
                            instrument=instrument,
                            entry_price=mr_result.entry_price,
                            stop_loss=mr_result.stop_loss,
                            risk_pct_override=settings.mr_risk_pct,
                            drawdown_scale=drawdown_monitor.scale_factor,
                            session_scale=_session_size_scale,
                            rolling_score_scale=_rolling_score_scale,
                            regime_scale=_regime_size_scale(mr_result.regime_state, "revert"),
                        )
    
                        mr_direction = Direction.LONG if mr_result.direction == "LONG" else Direction.SHORT
                        mr_order = OrderRequest(
                            instrument=instrument,
                            direction=mr_direction,
                            units=mr_units,
                            order_type=OrderType.LIMIT,
                            stop_loss=mr_result.stop_loss,
                            take_profit=mr_result.take_profit,
                            limit_price=mr_result.entry_price,
                            gtd_time=now + timedelta(hours=2),  # MR setups expire faster than trend
                        )
    
                        mr_order_id = await mr_order_manager.submit(mr_order)
                        pending_instruments.add(instrument)  # keep snapshot current for later engines this scan
                        logger.info(
                            "mr_order_submitted",
                            instrument=instrument,
                            direction=mr_result.direction,
                            units=mr_units,
                            entry=mr_result.entry_price,
                            sl=mr_result.stop_loss,
                            tp=mr_result.take_profit,
                            confluence=mr_result.confluence_score,
                            order_id=str(mr_order_id),
                        )
                        await _alerts.send_info(
                            f"MR Order placed: {mr_result.direction} {instrument}\n"
                            f"Entry: {mr_result.entry_price}  SL: {mr_result.stop_loss}  TP: {mr_result.take_profit}\n"
                            f"Units: {mr_units}  Confluence: {mr_result.confluence_score:.2f}\n"
                            f"Regime: RANGING  Expires: {(now + timedelta(hours=2)).strftime('%H:%M UTC')}"
                        )
                        await mr_session.commit()
                else:
                    logger.debug("mr_engine_disabled_skipping", instrument=instrument)

            except Exception as exc:
                logger.error("mr_scan_instrument_failed", instrument=instrument, error=str(exc))

            # ── London benchmark-fix continuation scan (paper-only sleeve) ──
            try:
                if settings.enable_fix_engine and instrument in set(fix_instruments):
                    async with _db_engine.AsyncSessionFactory() as fix_session:
                        fix_market_repo = MarketDataRepository(fix_session)
                        fix_signal_repo = SignalRepository(fix_session)
                        news_filter.calendar_repo = EconomicCalendarRepository(fix_session)

                        fix_h1 = await fix_market_repo.get_latest_n_candles(instrument, "H1", 80)
                        if len(fix_h1) < 30:
                            logger.debug("fix_scan_insufficient_data", instrument=instrument)
                            await fix_session.commit()
                        else:
                            fix_engine.update_cache(instrument, "H1", to_df(fix_h1))
                            fix_result = await fix_engine.evaluate(instrument, dt=now)
                            if fix_result.direction:
                                _fix_macro_overlay = await get_macro_state_overlay(
                                    redis_client, instrument, fix_result.direction
                                )
                                fix_result.metadata["macro_state_overlay"] = _fix_macro_overlay.to_metadata()
                                _fix_pos_overlay = await get_cot_positioning_overlay(
                                    redis_client, instrument, fix_result.direction
                                )
                                fix_result.metadata["positioning_overlay"] = _fix_pos_overlay.to_metadata()
                            else:
                                _fix_macro_overlay = None
                                _fix_pos_overlay = None

                            _in_fix_window = not fix_result.suppressed or not (
                                (fix_result.suppression_reason or "").startswith("FIX_OFF_WINDOW:")
                                or fix_result.suppression_reason == f"FIX_INSTRUMENT_EXCLUDED:{instrument}"
                            )
                            (logger.info if _in_fix_window else logger.debug)(
                                "fix_signal_evaluated",
                                instrument=instrument,
                                suppressed=fix_result.suppressed,
                                reason=fix_result.suppression_reason,
                                confluence=fix_result.confluence_score,
                                direction=fix_result.direction,
                                session=fix_result.session,
                                pre_move_pips=fix_result.pre_move_pips,
                            )

                            fix_meta = fix_result.metadata or {}
                            fix_row = SignalModel(
                                instrument=instrument,
                                timeframe="H1",
                                direction=fix_result.direction or "LONG",
                                confluence_score=fix_result.confluence_score,
                                rsi_score=fix_result.move_score,
                                bb_kc_score=None,
                                adx_score=fix_result.atr_score,
                                sr_score=None,
                                mtf_score=None,
                                csi_score=None,
                                ml_confidence=None,
                                regime_state=None,
                                session=fix_result.session,
                                suppressed=fix_result.suppressed,
                                suppression_reason=fix_result.suppression_reason,
                                signal_metadata=fix_meta,
                            )
                            fix_session.add(fix_row)
                            await fix_session.flush()

                            try:
                                await redis_client.publish("signals", json.dumps({
                                    "channel": "signals",
                                    "data": {
                                        "id":                 str(fix_row.id),
                                        "created_at":         fix_row.created_at.isoformat() if fix_row.created_at else None,
                                        "instrument":         instrument,
                                        "timeframe":          "H1",
                                        "direction":          fix_result.direction,
                                        "confluence_score":   float(fix_result.confluence_score),
                                        "rsi_score":          float(fix_result.move_score) if fix_result.move_score is not None else None,
                                        "bb_kc_score":        None,
                                        "adx_score":          float(fix_result.atr_score) if fix_result.atr_score is not None else None,
                                        "sr_score":           None,
                                        "mtf_score":          None,
                                        "csi_score":          None,
                                        "ml_confidence":      None,
                                        "regime_state":       None,
                                        "session":            fix_result.session,
                                        "suppressed":         fix_result.suppressed,
                                        "suppression_reason": fix_result.suppression_reason,
                                        "pre_move_pips":      fix_result.pre_move_pips,
                                        "fix_time_utc":       fix_meta.get("fix_time_utc"),
                                        "month_end_tag":      fix_meta.get("month_end_tag"),
                                        "macro_state_overlay": fix_meta.get("macro_state_overlay"),
                                        "positioning_overlay": fix_meta.get("positioning_overlay"),
                                    },
                                }))
                            except Exception as _ws_exc:
                                logger.warning("fix_ws_publish_failed", error=str(_ws_exc))

                            if not fix_result.suppressed:
                                _fix_session_start = now.replace(minute=0, second=0, microsecond=0)
                                _prior_fix_count = await fix_signal_repo.count_session_signals(
                                    instrument=instrument,
                                    session=FIX_SESSION,
                                    session_start=_fix_session_start,
                                    exclude_id=fix_row.id,
                                )
                                if _prior_fix_count > 0:
                                    logger.info(
                                        "fix_session_cap_skip",
                                        instrument=instrument,
                                        prior_signals=_prior_fix_count,
                                        session_start=_fix_session_start.isoformat(),
                                    )
                                elif settings.fix_paper_only:
                                    logger.info(
                                        "fix_signal_paper_only",
                                        instrument=instrument,
                                        direction=fix_result.direction,
                                        pre_move_pips=fix_result.pre_move_pips,
                                        month_end_tag=fix_meta.get("month_end_tag"),
                                    )
                                else:
                                    logger.warning(
                                        "fix_live_execution_not_implemented",
                                        instrument=instrument,
                                        direction=fix_result.direction,
                                    )
                            await fix_session.commit()
                else:
                    logger.debug("fix_engine_disabled_skipping", instrument=instrument)

            except Exception as exc:
                logger.error("fix_scan_instrument_failed", instrument=instrument, error=str(exc))

            # ── NFP continuation scan (paper-only event sleeve) ───────────────
            try:
                if settings.enable_nfp_engine and instrument in set(nfp_instruments) and recent_nfp_bundle is not None:
                    async with _db_engine.AsyncSessionFactory() as nfp_session:
                        nfp_market_repo = MarketDataRepository(nfp_session)
                        nfp_signal_repo = SignalRepository(nfp_session)

                        nfp_h1 = await nfp_market_repo.get_latest_n_candles(instrument, "H1", 80)
                        if len(nfp_h1) < 30:
                            await nfp_session.commit()
                        else:
                            nfp_engine.update_cache(instrument, "H1", to_df(nfp_h1))
                            nfp_result = nfp_engine.evaluate(instrument, recent_nfp_bundle, dt=now)
                            if (
                                nfp_result.suppressed
                                and (
                                    nfp_result.suppression_reason == "NFP_NO_RECENT_BUNDLE"
                                    or (nfp_result.suppression_reason or "").startswith("NFP_OFF_WINDOW:")
                                )
                            ):
                                await nfp_session.commit()
                            else:
                                nfp_meta = nfp_result.metadata or {}
                                nfp_row = SignalModel(
                                    instrument=instrument,
                                    timeframe="H1",
                                    direction=nfp_result.direction or "LONG",
                                    confluence_score=nfp_result.confluence_score,
                                    rsi_score=float(nfp_result.support_count),
                                    bb_kc_score=None,
                                    adx_score=float(nfp_result.surprise_mag) if nfp_result.surprise_mag is not None else None,
                                    sr_score=float(nfp_result.conflict_count),
                                    mtf_score=None,
                                    csi_score=None,
                                    ml_confidence=None,
                                    regime_state=None,
                                    session=nfp_result.session,
                                    suppressed=nfp_result.suppressed,
                                    suppression_reason=nfp_result.suppression_reason,
                                    signal_metadata=nfp_meta,
                                )
                                nfp_session.add(nfp_row)
                                await nfp_session.flush()

                                try:
                                    await redis_client.publish("signals", json.dumps({
                                        "channel": "signals",
                                        "data": {
                                            "id": str(nfp_row.id),
                                            "created_at": nfp_row.created_at.isoformat() if nfp_row.created_at else None,
                                            "instrument": instrument,
                                            "timeframe": "H1",
                                            "direction": nfp_result.direction,
                                            "confluence_score": float(nfp_result.confluence_score),
                                            "rsi_score": float(nfp_result.support_count),
                                            "adx_score": float(nfp_result.surprise_mag) if nfp_result.surprise_mag is not None else None,
                                            "sr_score": float(nfp_result.conflict_count),
                                            "session": nfp_result.session,
                                            "suppressed": nfp_result.suppressed,
                                            "suppression_reason": nfp_result.suppression_reason,
                                            "agreement": nfp_meta.get("agreement"),
                                            "surprise_bucket": nfp_meta.get("surprise_bucket"),
                                            "event_time_utc": nfp_meta.get("event_time_utc"),
                                            "expected_exit_time_utc": nfp_meta.get("expected_exit_time_utc"),
                                        },
                                    }))
                                except Exception as _ws_exc:
                                    logger.warning("nfp_ws_publish_failed", error=str(_ws_exc))

                                if not nfp_result.suppressed:
                                    _nfp_session_start = pd.Timestamp(nfp_meta["entry_time_utc"]).to_pydatetime().replace(
                                        minute=0, second=0, microsecond=0
                                    )
                                    _prior_nfp_count = await nfp_signal_repo.count_session_signals(
                                        instrument=instrument,
                                        session=NFP_SESSION,
                                        session_start=_nfp_session_start,
                                        exclude_id=nfp_row.id,
                                    )
                                    if _prior_nfp_count > 0:
                                        logger.info(
                                            "nfp_session_cap_skip",
                                            instrument=instrument,
                                            prior_signals=_prior_nfp_count,
                                            session_start=_nfp_session_start.isoformat(),
                                        )
                                    elif settings.nfp_paper_only:
                                        logger.info(
                                            "nfp_signal_paper_only",
                                            instrument=instrument,
                                            direction=nfp_result.direction,
                                            agreement=nfp_result.agreement,
                                            surprise_bucket=nfp_result.surprise_bucket,
                                        )
                                    else:
                                        logger.warning(
                                            "nfp_live_execution_not_implemented",
                                            instrument=instrument,
                                            direction=nfp_result.direction,
                                        )
                                await nfp_session.commit()
                else:
                    logger.debug("nfp_engine_disabled_or_no_bundle", instrument=instrument)

            except Exception as exc:
                logger.error("nfp_scan_instrument_failed", instrument=instrument, error=str(exc))

            # ── M15 trend-following scan (same engine, shorter timeframe) ──
            # M15 uses H1 as the higher-timeframe confirmation (instead of H4+D).
            # This generates 3-4× more signals per day. Same confluence threshold (0.65).
            # SL/TP use M15 ATR — smaller in $ terms but same 1% risk sizing.
            # GTD 1 hour (M15 setups go stale much faster than H1).
            try:
                if not settings.enable_m15_engine or instrument not in trend_instruments:
                    continue

                async with _db_engine.AsyncSessionFactory() as m15_session:
                    m15_market_repo  = MarketDataRepository(m15_session)
                    m15_order_repo   = OrderRepository(m15_session)
                    m15_order_manager = OrderManager(m15_order_repo, broker, redis=redis_client)
                    news_filter.calendar_repo = EconomicCalendarRepository(m15_session)

                    m15 = await m15_market_repo.get_latest_n_candles(instrument, "M15", 300)
                    if len(m15) < 50:
                        continue

                    # For M15 signals: use H1 as the "4H equivalent" and Daily as macro filter.
                    # Fetch independently — do not rely on h1/d1 from the trend block above,
                    # which may be unbound if trend is disabled or exited early for this instrument.
                    m15_h1 = await m15_market_repo.get_latest_n_candles(instrument, "H1", 200)
                    m15_d1 = await m15_market_repo.get_latest_n_candles(instrument, "D",  250)
                    m15_cache = {
                        "H1": {instrument: to_df(m15)},     # M15 bars → signal timeframe
                        "H4": {instrument: to_df(m15_h1)},  # H1 bars  → MTF confirmation
                        "D":  {instrument: to_df(m15_d1)},  # Daily stays as macro filter
                    }
                    engine.data_cache    = m15_cache
                    engine.hmm_detector  = _hmm_detectors.get(instrument)
                    engine.ml_classifier = None  # skip ML on M15 — not enough labeled data
                    engine.feature_engineer = None
                    engine.ood_detector  = None

                    m15_result = await engine.evaluate(instrument, dt=now)

                    # Restore H1 cache for next instrument iteration
                    engine.data_cache = {}

                    logger.debug(
                        "m15_signal_evaluated",
                        instrument=instrument,
                        suppressed=m15_result.suppressed,
                        reason=m15_result.suppression_reason,
                        confluence=m15_result.confluence_score,
                        session=m15_result.session,
                    )

                    if m15_result.suppressed:
                        continue

                    if settings.m15_paper_only:
                        logger.info("m15_signal_paper_only", instrument=instrument, direction=m15_result.direction)
                        await m15_session.commit()
                        continue

                    # Execution gates
                    _m15_dd_ok, _m15_dd_reason = drawdown_monitor.check()
                    if not _m15_dd_ok:
                        logger.warning("trade_blocked_drawdown", instrument=instrument, reason=_m15_dd_reason)
                        await _alerts.send_critical(f"Drawdown circuit breaker triggered\n{_m15_dd_reason}\nBalance: ${balance:.2f}")
                        await m15_session.commit()
                        continue
                    if daily_limiter.is_halted(balance):
                        logger.warning("trade_blocked_daily_limit", instrument=instrument)
                        await _alerts.send_critical(f"Daily loss limit hit — trading halted for today\nBalance: ${balance:.2f}")
                        await m15_session.commit()
                        continue
                    already_open = any(p.instrument == instrument for p in open_positions) or instrument in pending_instruments
                    if already_open:
                        continue
                    corr_ok, _ = correlation_mgr.check_new_position(
                        instrument, m15_result.direction, open_positions
                    )
                    if not corr_ok:
                        continue

                    # ATR from M15 data
                    m15_df = to_df(m15)
                    prev_close_m15 = m15_df["close"].shift(1)
                    tr_m15 = pd.concat([
                        m15_df["high"] - m15_df["low"],
                        (m15_df["high"] - prev_close_m15).abs(),
                        (m15_df["low"]  - prev_close_m15).abs(),
                    ], axis=1).max(axis=1)
                    atr_m15 = tr_m15.rolling(14).mean().iloc[-1]

                    close_m15 = float(m15_df["close"].iloc[-1])
                    if m15_result.direction == "LONG":
                        m15_entry = round(close_m15 - atr_m15 * 0.3, 5)
                        m15_sl    = round(m15_entry - 1.5 * atr_m15, 5)
                        m15_tp    = round(m15_entry + 2.0 * atr_m15, 5)
                    else:
                        m15_entry = round(close_m15 + atr_m15 * 0.3, 5)
                        m15_sl    = round(m15_entry + 1.5 * atr_m15, 5)
                        m15_tp    = round(m15_entry - 2.0 * atr_m15, 5)

                    m15_units = sizer.compute(
                        account_balance=balance,
                        instrument=instrument,
                        entry_price=m15_entry,
                        stop_loss=m15_sl,
                        risk_pct_override=settings.m15_risk_pct,
                        drawdown_scale=drawdown_monitor.scale_factor,
                        vix_scale=m15_result.vix_multiplier,
                        news_scale=m15_result.news_multiplier,
                        session_scale=_session_size_scale,
                        rolling_score_scale=_rolling_score_scale,
                        regime_scale=_regime_size_scale(m15_result.regime_state, "trend"),
                    )

                    m15_direction = Direction.LONG if m15_result.direction == "LONG" else Direction.SHORT
                    m15_order = OrderRequest(
                        instrument=instrument,
                        direction=m15_direction,
                        units=m15_units,
                        order_type=OrderType.LIMIT,
                        stop_loss=m15_sl,
                        take_profit=m15_tp,
                        limit_price=m15_entry,
                        gtd_time=now + timedelta(hours=1),  # M15 setups go stale fast
                    )

                    m15_order_id = await m15_order_manager.submit(m15_order)
                    pending_instruments.add(instrument)  # keep snapshot current for later engines this scan
                    logger.info(
                        "m15_order_submitted",
                        instrument=instrument,
                        direction=m15_result.direction,
                        units=m15_units,
                        entry=m15_entry,
                        sl=m15_sl,
                        tp=m15_tp,
                        confluence=m15_result.confluence_score,
                        order_id=str(m15_order_id),
                    )
                    await _alerts.send_info(
                        f"M15 Order: {m15_result.direction} {instrument}\n"
                        f"Entry: {m15_entry}  SL: {m15_sl}  TP: {m15_tp}\n"
                        f"Units: {m15_units}  Score: {m15_result.confluence_score:.2f}\n"
                        f"Expires: {(now + timedelta(hours=1)).strftime('%H:%M UTC')}"
                    )
                    await m15_session.commit()

            except Exception as exc:
                logger.error("m15_scan_instrument_failed", instrument=instrument, error=str(exc))

        # ── Refresh portfolio snapshot before LCR loop ─────────────────────────
        # The trend/MR/M15 instrument loop above may have submitted orders and
        # committed those sessions. Re-query so the LCR heat cap and open-position
        # checks reflect any orders placed earlier this scan tick.
        try:
            async with _db_engine.AsyncSessionFactory() as _refresh_session:
                _refresh_pos_repo   = PositionRepository(_refresh_session)
                _refresh_order_repo = OrderRepository(_refresh_session)
                open_positions      = await _refresh_pos_repo.get_open()
                _refresh_pending    = await _refresh_order_repo.get_pending()
                pending_instruments = {o.instrument for o in _refresh_pending}
        except Exception as _refresh_exc:
            logger.warning("portfolio_refresh_failed", error=str(_refresh_exc))
            # Proceed with the in-memory snapshot updated by pending_instruments.add() above.

        # ── Standalone LCR loop — must live outside the London-trend loop above.
        #    The trend loop fires `continue` for OFF_SESSION during NY hours (17–19 UTC),
        #    which would skip the LCR block entirely if it lived inside that loop.
        if now.hour in {17, 18, 19}:
            # ── Pair-level disable check (precommitted rules) ──────────────────
            # Evaluate all LCR pairs once per scan tick using the deterministic
            # disable/watchlist framework. DISABLED pairs are skipped; WATCHLIST
            # pairs still execute but are flagged in logs.
            _lcr_pair_statuses: dict = {}
            _lcr_status_loaded: bool = False
            try:
                from anchor.signals.lcr_pair_status import load_lcr_pair_statuses
                async with _db_engine.AsyncSessionFactory() as _ps_session:
                    _lcr_pair_statuses = await load_lcr_pair_statuses(_ps_session)
                _lcr_status_loaded = True
            except Exception as _ps_exc:
                logger.warning(
                    "lcr_pair_status_eval_failed",
                    error=str(_ps_exc),
                    action="skipping_lcr_session",
                )

            for instrument in LCR_INSTRUMENTS:
                # If pair status could not be loaded, we cannot enforce disable rules.
                # Skip all instruments this session rather than trading blind.
                if not _lcr_status_loaded:
                    break

                if instrument not in active_instruments:
                    continue

                # ── Pair-level disable gate ────────────────────────────────────
                _pair_status = _lcr_pair_statuses.get(instrument)
                if _pair_status is not None and _pair_status.status.value == "disabled":
                    logger.warning(
                        "lcr_pair_disabled_skip",
                        instrument=instrument,
                        reasons=_pair_status.reasons,
                    )
                    continue
                if _pair_status is not None and _pair_status.status.value == "watchlist":
                    logger.info(
                        "lcr_pair_watchlist",
                        instrument=instrument,
                        reasons=_pair_status.reasons,
                    )

                # ── Per-pair rolling 60-day PF circuit breaker ────────────────
                # Applies to all LCR pairs. A strategy can bleed slowly within
                # the drawdown halt limit — this catches that case.
                # Threshold: PF < 1.0 over last 60 days with >= 15 closed trades.
                # Fewer than 15 trades → insufficient data, pass through.
                try:
                    from sqlalchemy import text as _text
                    async with _db_engine.AsyncSessionFactory() as _cb_session:
                        cutoff_60d = now - timedelta(days=60)
                        _rows = (await _cb_session.execute(_text(
                            "SELECT net_pl FROM trades "
                            "WHERE instrument = :inst "
                            "AND closed_at >= :cutoff AND signal_id IS NOT NULL"
                        ), {"inst": instrument, "cutoff": cutoff_60d})).fetchall()
                    if len(_rows) >= 15:
                        _gross_win  = sum(r[0] for r in _rows if r[0] > 0)
                        _gross_loss = abs(sum(r[0] for r in _rows if r[0] <= 0))
                        _pf_60d     = _gross_win / _gross_loss if _gross_loss > 0 else float("inf")
                        if _pf_60d < 1.0:
                            logger.warning(
                                "lcr_rolling_pf_circuit_breaker",
                                instrument=instrument,
                                trades_60d=len(_rows),
                                pf_60d=round(_pf_60d, 3),
                            )
                            continue
                except Exception as _cb_exc:
                    logger.debug("lcr_rolling_pf_check_failed", instrument=instrument, error=str(_cb_exc))

                try:
                    async with _db_engine.AsyncSessionFactory() as lcr_session:
                        lcr_market_repo   = MarketDataRepository(lcr_session)
                        lcr_order_repo    = OrderRepository(lcr_session)
                        lcr_signal_repo   = SignalRepository(lcr_session)
                        lcr_order_manager = OrderManager(lcr_order_repo, broker, redis=redis_client)
                        news_filter.calendar_repo = EconomicCalendarRepository(lcr_session)

                        # Fetch candles fresh — independent of the trend-scan loop
                        lcr_h1 = await lcr_market_repo.get_latest_n_candles(instrument, "H1", 200)
                        lcr_d1 = await lcr_market_repo.get_latest_n_candles(instrument, "D", 250)

                        if len(lcr_h1) < 50:
                            logger.warning("lcr_scan_insufficient_data", instrument=instrument)
                            continue

                        lcr_engine.update_cache(instrument, "H1", to_df(lcr_h1))
                        lcr_engine.data_cache.setdefault("D", {})[instrument] = to_df(lcr_d1)
                        lcr_engine.hmm_detector = _hmm_detectors.get(instrument)

                        lcr_result = await lcr_engine.evaluate(instrument, dt=now)

                        _in_lcr_window = not lcr_result.suppressed or lcr_result.suppression_reason not in (
                            f"LCR_OFF_WINDOW:{now.hour:02d}UTC", f"LCR_INSTRUMENT_EXCLUDED:{instrument}"
                        )
                        (logger.info if _in_lcr_window else logger.debug)(
                            "lcr_signal_evaluated",
                            instrument=instrument,
                            suppressed=lcr_result.suppressed,
                            reason=lcr_result.suppression_reason,
                            confluence=lcr_result.confluence_score,
                            direction=lcr_result.direction,
                            session=lcr_result.session,
                        )

                        # Persist every LCR evaluation (audit trail) — map to Signal model:
                        # rsi_score=rsi, bb_kc_score=rejection, adx_score=range_pos, sr_score=range_qual
                        lcr_meta = lcr_result.metadata or {}
                        # Compute partial TP price (1.5:1 R toward TP) so it's auditable
                        _lcr_partial_tp_price = None
                        if (
                            lcr_result.entry_price is not None
                            and lcr_result.stop_loss is not None
                            and lcr_result.direction is not None
                        ):
                            _sl_d = abs(lcr_result.entry_price - lcr_result.stop_loss)
                            if lcr_result.direction == "LONG":
                                _lcr_partial_tp_price = round(lcr_result.entry_price + 1.5 * _sl_d, 5)
                            else:
                                _lcr_partial_tp_price = round(lcr_result.entry_price - 1.5 * _sl_d, 5)
                        lcr_meta.update({
                            "london_high":    lcr_result.london_high,
                            "london_low":     lcr_result.london_low,
                            "london_mid":     lcr_result.london_mid,
                            "atr":            lcr_result.atr,
                            "entry_price":    lcr_result.entry_price,
                            "stop_loss":      lcr_result.stop_loss,
                            "take_profit":    lcr_result.take_profit,
                            "partial_tp_price": _lcr_partial_tp_price,
                            "strategy":       "LCR",
                        })
                        lcr_row = SignalModel(
                            instrument=instrument,
                            timeframe="H1",
                            direction=lcr_result.direction or "LONG",
                            confluence_score=lcr_result.confluence_score,
                            rsi_score=lcr_result.rsi_score or None,
                            bb_kc_score=lcr_result.rejection_score or None,
                            adx_score=lcr_result.range_pos_score or None,
                            sr_score=lcr_result.range_qual_score or None,
                            mtf_score=None,
                            csi_score=None,
                            ml_confidence=None,
                            regime_state=None,
                            session=lcr_result.session,
                            suppressed=lcr_result.suppressed,
                            suppression_reason=lcr_result.suppression_reason,
                            signal_metadata=lcr_meta,
                        )
                        lcr_session.add(lcr_row)
                        await lcr_session.flush()

                        # Broadcast to WebSocket so dashboard LCR panel updates in real-time
                        try:
                            await redis_client.publish("signals", json.dumps({
                                "channel": "signals",
                                "data": {
                                    "id":                    str(lcr_row.id),
                                    "created_at":            lcr_row.created_at.isoformat() if lcr_row.created_at else None,
                                    "instrument":            instrument,
                                    "timeframe":             "H1",
                                    "direction":             lcr_result.direction,
                                    "confluence_score":      float(lcr_result.confluence_score),
                                    "rsi_score":             float(lcr_result.rsi_score)        if lcr_result.rsi_score        else None,
                                    "bb_kc_score":           float(lcr_result.rejection_score)  if lcr_result.rejection_score  else None,
                                    "adx_score":             float(lcr_result.range_pos_score)  if lcr_result.range_pos_score  else None,
                                    "sr_score":              float(lcr_result.range_qual_score) if lcr_result.range_qual_score else None,
                                    "mtf_score":             None,
                                    "csi_score":             None,
                                    "cot_score":             None,
                                    "rate_divergence_score": None,
                                    "ml_confidence":         None,
                                    "regime_state":          None,
                                    "session":               lcr_result.session,
                                    "suppressed":            lcr_result.suppressed,
                                    "suppression_reason":    lcr_result.suppression_reason,
                                    "london_high":           lcr_result.london_high,
                                    "london_low":            lcr_result.london_low,
                                    "london_mid":            lcr_result.london_mid,
                                    "position_in_range":     lcr_meta.get("position_in_range"),
                                },
                            }))
                        except Exception as _ws_exc:
                            logger.warning("lcr_ws_publish_failed", error=str(_ws_exc))

                        if lcr_result.suppressed:
                            await lcr_session.commit()
                            continue

                        if not settings.enable_lcr_engine:
                            logger.info("lcr_engine_disabled", instrument=instrument)
                            await lcr_session.commit()
                            continue

                        if settings.lcr_paper_only:
                            logger.info("lcr_signal_paper_only", instrument=instrument, direction=lcr_result.direction)
                            await lcr_session.commit()
                            continue

                        # Execution gates
                        _lcr_dd_ok, _lcr_dd_reason = drawdown_monitor.check()
                        if not _lcr_dd_ok:
                            logger.warning("trade_blocked_drawdown", instrument=instrument, reason=_lcr_dd_reason)
                            await _alerts.send_critical(f"Drawdown circuit breaker triggered\n{_lcr_dd_reason}\nBalance: ${balance:.2f}")
                            await lcr_session.commit()
                            continue
                        if daily_limiter.is_halted(balance):
                            logger.warning("trade_blocked_daily_limit", instrument=instrument)
                            await _alerts.send_critical(f"Daily loss limit hit — trading halted for today\nBalance: ${balance:.2f}")
                            await lcr_session.commit()
                            continue
                        # Pending orders: never interfere — skip
                        if instrument in pending_instruments:
                            await lcr_session.commit()
                            continue

                        # Open position on this instrument?
                        open_london = next((p for p in open_positions if p.instrument == instrument), None)
                        if open_london is not None:
                            if open_london.direction.value == lcr_result.direction:
                                # Already positioned the same way — skip LCR
                                await lcr_session.commit()
                                continue
                            else:
                                # Opposite direction: London exhausted, LCR reversal firing.
                                # Close the London trade first, then open LCR.
                                if not open_london.oanda_trade_id:
                                    # Can't close without trade ID — skip safely
                                    await lcr_session.commit()
                                    continue
                                try:
                                    await broker.close_trade(open_london.oanda_trade_id)
                                    logger.info(
                                        "lcr_closed_london_for_reversal",
                                        instrument=instrument,
                                        london_direction=open_london.direction.value,
                                        lcr_direction=lcr_result.direction,
                                        trade_id=open_london.oanda_trade_id,
                                    )
                                except Exception as _close_exc:
                                    logger.warning(
                                        "lcr_close_london_failed",
                                        instrument=instrument,
                                        error=str(_close_exc),
                                    )
                                    await lcr_session.commit()
                                    continue

                        # Correlation check against remaining open positions (excl. just-closed London)
                        effective_open = [p for p in open_positions if p.instrument != instrument]
                        corr_ok, _ = correlation_mgr.check_new_position(
                            instrument, lcr_result.direction, effective_open
                        )
                        if not corr_ok:
                            await lcr_session.commit()
                            continue

                        # Portfolio heat cap: never hold more than max_concurrent_lcr_positions
                        # open at once. Since only LCR is active, total open == LCR open.
                        # At 1% base risk: 3 positions cap = max 3% gross portfolio heat.
                        # Use effective_open (which excludes the just-closed London reversal position)
                        # so a valid reversal replacement is not incorrectly blocked.
                        if len(effective_open) >= settings.max_concurrent_lcr_positions:
                            logger.info(
                                "lcr_max_concurrent_skip",
                                instrument=instrument,
                                open_count=len(effective_open),
                                max_allowed=settings.max_concurrent_lcr_positions,
                            )
                            await lcr_session.commit()
                            continue

                        # EUR_JPY: paper-observe only — signal/SL/TP logged but no live order submitted.
                        # Live data showed 2/2 EUR_JPY trades were intrabar stops (< 15 min).
                        # Re-enable only after 20+ paper trades with PF > 1.0.
                        if instrument == "EUR_JPY":
                            logger.info(
                                "lcr_eurjpy_paper_only",
                                instrument=instrument,
                                direction=lcr_result.direction,
                                confluence=lcr_result.confluence_score,
                            )
                            await lcr_session.commit()
                            continue

                        # One-signal-per-instrument-per-session cap.
                        # LCR can produce a signal at 17:00, 18:00, and 19:00 on the same pair
                        # using the same London range and the same directional thesis.
                        # A second attempt after the first has already fired is not an independent
                        # trade — it's averaging down on a failed setup. Cap at 1.
                        _lcr_session_start = now.replace(hour=17, minute=0, second=0, microsecond=0)
                        _prior_lcr_count = await lcr_signal_repo.count_lcr_session_signals(
                            instrument, _lcr_session_start, exclude_id=lcr_row.id
                        )
                        if _prior_lcr_count > 0:
                            logger.info(
                                "lcr_session_cap_skip",
                                instrument=instrument,
                                prior_signals=_prior_lcr_count,
                                session_start=_lcr_session_start.isoformat(),
                            )
                            await lcr_session.commit()
                            continue

                        # SL/TP computed inside LCR engine (london extreme + ATR buffer → london mid)
                        # USD_CAD: reduced to 0.5% risk — live underperformance review 2026-03-23.
                        # This is a pair-specific underperformance penalty, not a correlation adjustment.
                        _lcr_risk_scale = 0.50 if instrument in {"USD_CAD"} else 1.0
                        # Continuous correlation scaling: use the live 45-day correlation matrix to
                        # compute how much independent risk the new position actually adds.
                        # scale = max(0.35, 1.0 - max_effective_overlap) where
                        # effective_overlap = corr × direction_alignment with each open position.
                        # Replaces the previous binary 2-open-dollar-pairs → 0.5x rule.
                        # Passed as correlation_scale (separate parameter) so it's tracked independently.
                        _corr_scale = correlation_mgr.compute_scale_factor(
                            instrument, lcr_result.direction, effective_open
                        )
                        if _corr_scale < 1.0:
                            logger.info(
                                "lcr_correlation_scale_applied",
                                instrument=instrument,
                                direction=lcr_result.direction,
                                correlation_scale=round(_corr_scale, 3),
                            )
                        # DXY scaling: strong USD momentum (|5d change| > 1.5%) reduces LCR size
                        # on USD pairs by 30% — mean reversion less reliable during macro trends
                        _USD_PAIRS = {"EUR_USD", "GBP_USD", "NZD_USD", "USD_CAD", "AUD_USD"}
                        if instrument in _USD_PAIRS:
                            try:
                                import json as _json
                                _dxy_raw = await redis_client.get("dxy_data")
                                if _dxy_raw:
                                    _dxy = _json.loads(_dxy_raw)
                                    if abs(_dxy.get("change_5d_pct", 0)) > 1.5:
                                        _lcr_risk_scale *= 0.70
                                        logger.info("lcr_dxy_scale_applied",
                                                    instrument=instrument,
                                                    dxy_change=_dxy.get("change_5d_pct"))
                            except Exception as _exc:
                                logger.debug("lcr_dxy_redis_read_failed", instrument=instrument, error=str(_exc))
                        lcr_units = sizer.compute(
                            account_balance=balance,
                            instrument=instrument,
                            entry_price=lcr_result.entry_price,
                            stop_loss=lcr_result.stop_loss,
                            risk_pct_override=settings.lcr_risk_pct,
                            correlation_scale=_corr_scale,
                            drawdown_scale=drawdown_monitor.scale_factor * _lcr_risk_scale,
                            session_scale=_session_size_scale,
                            rolling_score_scale=_rolling_score_scale,
                            regime_scale=_regime_size_scale(lcr_result.regime_state, "revert"),
                        )

                        lcr_direction = Direction.LONG if lcr_result.direction == "LONG" else Direction.SHORT
                        lcr_order = OrderRequest(
                            instrument=instrument,
                            direction=lcr_direction,
                            units=lcr_units,
                            order_type=OrderType.LIMIT,
                            stop_loss=lcr_result.stop_loss,
                            take_profit=lcr_result.take_profit,
                            limit_price=lcr_result.entry_price,
                            gtd_time=now + timedelta(hours=2),  # LCR resolves within NY session
                            signal_id=lcr_row.id,
                        )

                        lcr_order_id = await lcr_order_manager.submit(lcr_order)
                        logger.info(
                            "lcr_order_submitted",
                            instrument=instrument,
                            direction=lcr_result.direction,
                            units=lcr_units,
                            entry=lcr_result.entry_price,
                            sl=lcr_result.stop_loss,
                            tp=lcr_result.take_profit,
                            london_mid=lcr_result.london_mid,
                            confluence=lcr_result.confluence_score,
                            order_id=str(lcr_order_id),
                        )
                        await _alerts.send_info(
                            f"LCR Order: {lcr_result.direction} {instrument}\n"
                            f"Entry: {lcr_result.entry_price}  SL: {lcr_result.stop_loss}  TP: {lcr_result.take_profit}\n"
                            f"Units: {lcr_units}  Score: {lcr_result.confluence_score:.2f}\n"
                            f"London mid (target): {lcr_result.london_mid}\n"
                            f"Expires: {(now + timedelta(hours=2)).strftime('%H:%M UTC')}"
                        )
                        await lcr_session.commit()

                except Exception as exc:
                    logger.error("lcr_scan_instrument_failed", instrument=instrument, error=str(exc))

        await redis_client.aclose()
        sync_redis.close()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("signal_scan_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)
