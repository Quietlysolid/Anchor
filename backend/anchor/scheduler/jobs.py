"""Celery task definitions for all scheduled jobs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from anchor.scheduler.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Module-level singletons — survive across task invocations within the same worker process.
# DrawdownMonitor MUST be a singleton so _peak_equity accumulates correctly.
# DailyLimiter MUST be a singleton so the daily P&L accumulates within a trading day.
from anchor.risk.drawdown_monitor import DrawdownMonitor as _DrawdownMonitor
from anchor.risk.daily_limiter import DailyLimiter as _DailyLimiter
from anchor.risk.spread_monitor import SpreadMonitor as _SpreadMonitor
from anchor.monitoring.alerts import AlertService as _AlertService

_drawdown_monitor = _DrawdownMonitor()
_daily_limiter = _DailyLimiter()
_spread_monitor = _SpreadMonitor()
_alerts = _AlertService()


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="anchor.scheduler.jobs.snapshot_equity", bind=True, max_retries=3)
def snapshot_equity(self):
    """Write an equity curve point every 15 minutes."""
    async def _inner():
        from anchor.config import settings
        from anchor.database.engine import init_db, get_session
        from anchor.database.repositories import EquityRepository
        from anchor.database.models import EquityCurvePoint
        from anchor.execution.broker_client import BrokerClient

        await init_db()
        client = BrokerClient()
        account = await client.get_account_summary()
        if not account:
            logger.warning("snapshot_equity_no_account")
            return

        balance = float(account.get("balance", 0))
        equity = float(account.get("NAV", balance))
        unrealized = equity - balance

        async with get_session() as session:
            repo = EquityRepository(session)
            latest = await repo.get_latest()
            peak = max(equity, float(latest.peak_equity) if latest else equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0

            point = EquityCurvePoint(
                time=datetime.now(timezone.utc),
                account_balance=balance,
                account_equity=equity,
                unrealized_pl=unrealized,
                drawdown_pct=dd * 100,
                peak_equity=peak,
            )
            await repo.insert(point)
            await session.commit()

        # Update Prometheus
        from anchor.monitoring import metrics
        metrics.account_balance.set(balance)
        metrics.account_equity.set(equity)
        metrics.unrealized_pl.set(unrealized)
        metrics.drawdown_pct.set(dd * 100)

        logger.debug("equity_snapshot_written", balance=balance, equity=equity)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("snapshot_equity_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="anchor.scheduler.jobs.import_economic_calendar", bind=True, max_retries=2)
def import_economic_calendar(self):
    """Import ForexFactory calendar for next 7 days."""
    async def _inner():
        from anchor.data.forex_factory import ForexFactoryScraper
        from anchor.database.engine import init_db, get_session
        from anchor.database.repositories import EconomicCalendarRepository
        from datetime import date

        await init_db()

        async with ForexFactoryScraper() as scraper:
            events = await scraper.fetch_week()

        if not events:
            return

        async with get_session() as session:
            repo = EconomicCalendarRepository(session)
            count = await repo.insert_many(events)
            await session.commit()
        logger.info("calendar_imported", events=count)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("calendar_import_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.update_fred_rates", bind=True, max_retries=3)
def update_fred_rates(self):
    """Fetch FRED central bank policy rates and store rate differentials in Redis.

    Key: fred_rate_diff  TTL: 25 hours.
    Runs daily — rates change at most once per central bank meeting (~6 weeks).
    """
    async def _inner():
        from anchor.config import settings
        from anchor.data.fred_rates import fetch_and_store, fetch_and_store_dxy
        import redis.asyncio as aioredis

        if not settings.fred_api_key:
            logger.warning("fred_api_key_not_set")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            result = await fetch_and_store(redis_client, settings.fred_api_key)
            available = [k for k, v in result.items() if v["available"]]
            logger.info("fred_rates_updated", available=available)
            await fetch_and_store_dxy(redis_client, settings.fred_api_key)
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("fred_rates_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=3_600)  # retry in 1 hour


@celery_app.task(name="anchor.scheduler.jobs.update_cot_data", bind=True, max_retries=2)
def update_cot_data(self):
    """Download and store latest CFTC COT report in Redis.

    Key: cot_data  TTL: 8 days (COT is weekly; 8-day TTL survives holiday delays).
    """
    async def _inner():
        import json
        from anchor.config import settings
        from anchor.data.cot_parser import CotParser
        import redis.asyncio as aioredis

        async with CotParser() as parser:
            result = await parser.fetch_latest()

        if not result:
            logger.warning("cot_empty_result")
            return

        # Serialise datetime → ISO strings so json.dumps works
        serialisable = {
            currency: {
                "net_noncommercial": data["net_noncommercial"],
                "net_commercial":    data["net_commercial"],
                "report_date":       data["report_date"].isoformat() if data.get("report_date") else None,
            }
            for currency, data in result.items()
        }

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis_client.set("cot_data", json.dumps(serialisable), ex=8 * 86_400)
        finally:
            await redis_client.aclose()

        logger.info("cot_persisted", currencies=list(result.keys()))

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("cot_update_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=600)


@celery_app.task(name="anchor.scheduler.jobs.update_economic_surprise", bind=True, max_retries=2)
def update_economic_surprise(self):
    """Compute per-currency economic surprise scores and cache in Redis.

    Reads recent HIGH-impact releases (actual vs forecast) from the DB,
    computes a rolling surprise score per currency, and writes to Redis.
    Keys: econ_surprise:{CCY}  TTL: 1 hour.
    Runs every 30 minutes — often no new releases, but cost is a single DB read.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.database.engine import init_db, get_session
        from anchor.database.repositories.events import EconomicCalendarRepository
        from anchor.signals.economic_surprise import update_surprise_cache
        import redis.asyncio as aioredis

        await init_db()
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            async with get_session() as session:
                repo = EconomicCalendarRepository(session)
                await update_surprise_cache(redis_client, repo)
            logger.info("economic_surprise_cache_updated")
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("economic_surprise_update_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.reconcile_positions", bind=True, max_retries=3)
def reconcile_positions(self):
    """Reconcile DB positions vs OANDA ground truth every 15 minutes."""
    async def _inner():
        from anchor.database.engine import init_db
        from anchor.database.repositories.positions import PositionRepository
        from anchor.database.repositories.orders import OrderRepository
        from anchor.database.repositories.events import SystemEventRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.execution.reconciler import Reconciler

        await init_db()
        import anchor.database.engine as _db_engine

        client = BrokerClient()
        async with _db_engine.AsyncSessionFactory() as session:
            reconciler = Reconciler(
                broker_client=client,
                position_repo=PositionRepository(session),
                order_repo=OrderRepository(session),
                system_event_repo=SystemEventRepository(session),
                daily_limiter=_daily_limiter,
            )
            report = await reconciler.reconcile()
            await session.commit()
        actions = len(report.get("actions_taken", []))
        if actions:
            logger.info("reconciliation_complete", actions=actions, report=report)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("reconcile_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


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
        from anchor.database.models import Signal as SignalModel
        from anchor.signals.engine import ConfluenceEngine
        from anchor.signals.mean_reversion_engine import MeanReversionEngine
        from anchor.signals.news_filter import NewsFilter
        from anchor.database.repositories import EconomicCalendarRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.execution.order_manager import OrderManager
        from anchor.execution.order_types import OrderRequest, Direction, OrderType
        from anchor.risk.position_sizer import PositionSizer
        from anchor.risk.correlation import CorrelationManager
        from anchor.utils.time_utils import utcnow
        from anchor.utils.math_utils import get_pip_size
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

        broker = BrokerClient()
        sizer = PositionSizer()
        drawdown_monitor = _drawdown_monitor   # persistent singleton
        daily_limiter = _daily_limiter         # persistent singleton
        correlation_mgr = CorrelationManager()

        # Get live account state once for the whole scan
        account = await broker.get_account_summary()
        balance = float(account.get("balance", 0))
        equity  = float(account.get("NAV", balance))

        # Bootstrap peak equity from DB on the first scan after a worker restart
        # so the drawdown circuit breaker doesn't silently reset to the current equity.
        if drawdown_monitor._peak_equity is None:
            async with _db_engine.AsyncSessionFactory() as _bootstrap_session:
                await drawdown_monitor.bootstrap_peak_equity(_bootstrap_session)

        drawdown_monitor.update(equity)

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

        # Wire Redis into spread monitor so it can read cross-process spread data
        # (stream runs in the FastAPI process; worker has a separate in-memory instance)
        _spread_monitor.set_redis(redis_client)

        # Tier 1: read session quality once per scan cycle (written at 06:30 UTC presession brief)
        # Defaults to MIXED/1.0 if key is absent — fail-open, no behavioural impact
        _session_size_scale = 1.0
        try:
            import json as _json_sq
            from anchor.intelligence.session_quality import _REDIS_KEY as _SQ_KEY
            _sq_raw = await redis_client.get(_SQ_KEY)
            if _sq_raw:
                _sq_data = _json_sq.loads(_sq_raw)
                _session_size_scale = max(0.5, min(1.0, float(_sq_data.get("size_scale", 1.0))))
                logger.debug(
                    "scan_session_quality_loaded",
                    environment=_sq_data.get("environment"),
                    size_scale=_session_size_scale,
                )
        except Exception as _sq_err:
            logger.warning("scan_session_quality_read_failed", error=str(_sq_err))

        # Tier 2b: rolling session score scale (0.75 if 5-session avg < 5.0, else 1.0)
        _rolling_score_scale = 1.0
        try:
            _raw_avg = await redis_client.get("rolling_score_avg")
            if _raw_avg is not None:
                _avg = float(_raw_avg)
                if _avg < 5.0:
                    _rolling_score_scale = 0.75
                logger.debug("scan_rolling_score_loaded", avg=round(_avg, 2), scale=_rolling_score_scale)
        except Exception as _rs_err:
            logger.warning("scan_rolling_score_read_failed", error=str(_rs_err))

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
        for _inst in settings.instruments:
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
            for inst in settings.instruments:
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
        for _inst in settings.instruments:
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

        # London Close Reversal engine: NY session (17:00–19:59 UTC), EUR/GBP/JPY only
        from anchor.signals.london_close_reversion import LondonCloseReversionEngine, LCR_INSTRUMENTS
        lcr_engine = LondonCloseReversionEngine(
            news_filter=news_filter,
            spread_monitor=_spread_monitor,
            drawdown_monitor=drawdown_monitor,
            hmm_detector=None,  # injected per-instrument inside the scan loop below
        )

        # Evaluate + persist each instrument in its own isolated session
        # so one DB error doesn't poison the others
        for instrument in settings.instruments:
            try:
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
                    mr_engine.update_cache(instrument, "H1", to_df(h1))
                    mr_engine.update_cache(instrument, "D",  to_df(d1))

                    # Inject instrument-specific classifier and HMM (None → engine falls back gracefully)
                    engine.hmm_detector   = _hmm_detectors.get(instrument)
                    mr_engine.hmm_detector = _hmm_detectors.get(instrument)
                    engine.ml_classifier  = _classifiers.get(instrument)
                    if not engine.ml_classifier:
                        engine.feature_engineer = None
                        engine.ood_detector = None
                    else:
                        engine.feature_engineer = feature_engineer
                        engine.ood_detector = _ood_detectors.get(instrument)

                    result = await engine.evaluate(instrument, dt=now)

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

                    # ── Execution gate ────────────────────────────────────────

                    # 1. Drawdown circuit breaker
                    dd_ok, dd_reason = drawdown_monitor.check()
                    if not dd_ok:
                        logger.warning("trade_blocked_drawdown", instrument=instrument, reason=dd_reason)
                        await _alerts.send_critical(f"Drawdown circuit breaker triggered\n{dd_reason}\nBalance: ${balance:.2f}")
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
                        kelly_fraction=float(result.ml_confidence) if result.ml_confidence else None,
                        drawdown_scale=drawdown_monitor.scale_factor,
                        vix_scale=result.vix_multiplier,
                        news_scale=result.news_multiplier,
                        session_scale=_session_size_scale,
                        rolling_score_scale=_rolling_score_scale,
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

            except Exception as exc:
                logger.error("signal_scan_instrument_failed", instrument=instrument, error=str(exc))
                await _alerts.send_warning(f"Signal scan failed for {instrument}\n{exc}")

            # ── Mean-reversion scan (separate try block — trend failure must not block MR) ──
            try:
                async with _db_engine.AsyncSessionFactory() as mr_session:
                    mr_order_repo    = OrderRepository(mr_session)
                    mr_order_manager = OrderManager(mr_order_repo, broker, redis=redis_client)
                    news_filter.calendar_repo = EconomicCalendarRepository(mr_session)

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

                    # Execution gates (same as trend engine)
                    if not drawdown_monitor.check()[0]:
                        continue
                    if daily_limiter.is_halted(balance):
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
                        drawdown_scale=drawdown_monitor.scale_factor,
                        session_scale=_session_size_scale,
                        rolling_score_scale=_rolling_score_scale,
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

            except Exception as exc:
                logger.error("mr_scan_instrument_failed", instrument=instrument, error=str(exc))

            # ── M15 trend-following scan (same engine, shorter timeframe) ──
            # M15 uses H1 as the higher-timeframe confirmation (instead of H4+D).
            # This generates 3-4× more signals per day. Same confluence threshold (0.65).
            # SL/TP use M15 ATR — smaller in $ terms but same 1% risk sizing.
            # GTD 1 hour (M15 setups go stale much faster than H1).
            try:
                async with _db_engine.AsyncSessionFactory() as m15_session:
                    m15_market_repo  = MarketDataRepository(m15_session)
                    m15_order_repo   = OrderRepository(m15_session)
                    m15_order_manager = OrderManager(m15_order_repo, broker, redis=redis_client)
                    news_filter.calendar_repo = EconomicCalendarRepository(m15_session)

                    m15 = await m15_market_repo.get_latest_n_candles(instrument, "M15", 300)
                    if len(m15) < 50:
                        continue

                    # For M15 signals: use H1 as the "4H equivalent" and H4 as "Daily equivalent"
                    # The engine._get_data() reads from data_cache, so we inject M15 as H1
                    # and shift the existing H1 into the H4 slot for this scan only.
                    m15_cache = {
                        "H1": {instrument: to_df(m15)},   # M15 bars → signal timeframe
                        "H4": {instrument: to_df(h1)},    # H1 bars  → MTF confirmation
                        "D":  {instrument: to_df(d1)},    # Daily stays as macro filter
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

                    # Execution gates
                    if not drawdown_monitor.check()[0]:
                        continue
                    if daily_limiter.is_halted(balance):
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
                        drawdown_scale=drawdown_monitor.scale_factor,
                        vix_scale=m15_result.vix_multiplier,
                        news_scale=m15_result.news_multiplier,
                        session_scale=_session_size_scale,
                        rolling_score_scale=_rolling_score_scale,
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

        # ── Standalone LCR loop — must live outside the London-trend loop above.
        #    The trend loop fires `continue` for OFF_SESSION during NY hours (17–19 UTC),
        #    which would skip the LCR block entirely if it lived inside that loop.
        if now.hour in {17, 18, 19}:
            for instrument in LCR_INSTRUMENTS:
                if instrument not in settings.instruments:
                    continue

                # ── EUR/JPY rolling 60-day PF circuit breaker ─────────────────
                # Walk-forward showed EUR_JPY fails in non-JPY regimes (2018, 2019).
                # If PF < 1.0 over the last 60 days, pause EUR_JPY LCR until it recovers.
                if instrument == "EUR_JPY":
                    try:
                        from sqlalchemy import text as _text
                        async with _db_engine.AsyncSessionFactory() as _cb_session:
                            cutoff_60d = now - timedelta(days=60)
                            _rows = (await _cb_session.execute(_text(
                                "SELECT net_pl FROM trades "
                                "WHERE instrument = 'EUR_JPY' "
                                "AND closed_at >= :cutoff AND signal_id IS NOT NULL"
                            ), {"cutoff": cutoff_60d})).fetchall()
                        if len(_rows) >= 10:   # need at least 10 trades to judge
                            _gross_win  = sum(r[0] for r in _rows if r[0] > 0)
                            _gross_loss = abs(sum(r[0] for r in _rows if r[0] <= 0))
                            _pf_60d     = _gross_win / _gross_loss if _gross_loss > 0 else float("inf")
                            if _pf_60d < 1.0:
                                logger.warning(
                                    "eurjpy_circuit_breaker_active",
                                    trades_60d=len(_rows),
                                    pf_60d=round(_pf_60d, 3),
                                )
                                continue
                    except Exception as _cb_exc:
                        logger.debug("eurjpy_circuit_breaker_check_failed", error=str(_cb_exc))

                try:
                    async with _db_engine.AsyncSessionFactory() as lcr_session:
                        lcr_market_repo   = MarketDataRepository(lcr_session)
                        lcr_order_repo    = OrderRepository(lcr_session)
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
                        lcr_meta.update({
                            "london_high":  lcr_result.london_high,
                            "london_low":   lcr_result.london_low,
                            "london_mid":   lcr_result.london_mid,
                            "atr":          lcr_result.atr,
                            "entry_price":  lcr_result.entry_price,
                            "stop_loss":    lcr_result.stop_loss,
                            "take_profit":  lcr_result.take_profit,
                            "strategy":     "LCR",
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

                        # Execution gates
                        if not drawdown_monitor.check()[0]:
                            await lcr_session.commit()
                            continue
                        if daily_limiter.is_halted(balance):
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

                        # SL/TP computed inside LCR engine (london extreme + ATR buffer → london mid)
                        # EUR_JPY uses 0.75% risk — failed 2018+2019 in walk-forward (JPY cross weakness)
                        # USD_CAD: walk-forward confirmed 7/7 profitable, lowest DD (-15.2%) → full 1% risk
                        _lcr_risk_scale = 0.75 if instrument in {"EUR_JPY"} else 1.0
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
                            except Exception:
                                pass
                        lcr_units = sizer.compute(
                            account_balance=balance,
                            instrument=instrument,
                            entry_price=lcr_result.entry_price,
                            stop_loss=lcr_result.stop_loss,
                            drawdown_scale=drawdown_monitor.scale_factor * _lcr_risk_scale,
                            session_scale=_session_size_scale,
                            rolling_score_scale=_rolling_score_scale,
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


@celery_app.task(name="anchor.scheduler.jobs.import_candles", bind=True, max_retries=3)
def import_candles(self):
    """Import latest H1, H4, and D candles from OANDA for all instruments.

    Runs every hour to keep the candle cache fresh for signal scanning.
    Fetches only the last ~200 H1 / 100 H4 / 60 D candles (fast incremental).
    """
    async def _inner():
        from datetime import timezone
        from anchor.config import settings
        from anchor.database.engine import init_db
        from anchor.database.repositories.market_data import MarketDataRepository
        from anchor.data.oanda_history import OANDAHistoryClient

        await init_db()
        import anchor.database.engine as _db_engine

        client = OANDAHistoryClient()
        end = datetime.now(timezone.utc)

        # How far back to pull per timeframe (enough to keep signal scan fed)
        lookback = {
            "M15": timedelta(days=3),  # ~288 M15 bars (3 days × 96 bars/day)
            "H1":  timedelta(days=9),  # ~200 H1 bars
            "H4":  timedelta(days=17), # ~100 H4 bars
            "D":   timedelta(days=270),# ~250 D bars (need 200+ for SMA(200) in MTF check)
        }

        for instrument in settings.instruments:
            for tf, delta in lookback.items():
                try:
                    df = await client.fetch_candles(
                        instrument=instrument,
                        granularity=tf,
                        start=end - delta,
                        end=end,
                    )
                    if df.empty:
                        continue

                    async with _db_engine.AsyncSessionFactory() as session:
                        repo = MarketDataRepository(session)
                        await repo.bulk_insert(instrument, tf, df)
                        await session.commit()

                    logger.info(
                        "candles_imported",
                        instrument=instrument,
                        timeframe=tf,
                        rows=len(df),
                    )
                except Exception as exc:
                    logger.error(
                        "candle_import_failed",
                        instrument=instrument,
                        timeframe=tf,
                        error=str(exc),
                    )

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("import_candles_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="anchor.scheduler.jobs.run_regime_detection", bind=True, max_retries=2)
def run_regime_detection(self):
    """Fit/update HMM regime detector for all instruments and publish to WebSocket."""
    async def _inner():
        import json
        from anchor.config import settings
        from anchor.database.engine import init_db, AsyncSessionFactory
        from anchor.database.repositories.market_data import MarketDataRepository
        from anchor.regime.hmm_detector import HMMRegimeDetector

        import pandas as pd
        import redis.asyncio as aioredis

        await init_db()
        import anchor.database.engine as _db_engine
        from pathlib import Path as _HMMPath
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        regime_snapshot = {}

        async with _db_engine.AsyncSessionFactory() as session:
            repo = MarketDataRepository(session)
            for instrument in settings.instruments:
                try:
                    rows = await repo.get_latest_n_candles(instrument, "D", 300)
                    if len(rows) < 60:
                        logger.warning("regime_insufficient_data", instrument=instrument, rows=len(rows))
                        continue

                    df = pd.DataFrame([{
                        "time": r.time, "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close),
                    } for r in rows]).set_index("time")

                    # Each instrument gets its own HMM — fit fresh each time so the
                    # model reflects this instrument's specific volatility dynamics.
                    detector = HMMRegimeDetector()
                    detector.fit(df)

                    # Save per-instrument model + a shared "latest" for backwards compat
                    _model_dir = _HMMPath("/app/models")
                    _model_dir.mkdir(parents=True, exist_ok=True)
                    detector.save(_model_dir / f"hmm_{instrument}.pkl")
                    # Also overwrite the shared fallback so single-model consumers still work
                    detector.save(_model_dir / "hmm_latest.pkl")

                    state, confidence = detector.predict_current(df)
                    regime_snapshot[instrument] = {"state": state, "confidence": round(confidence, 4)}

                    logger.info("regime_detected", instrument=instrument, state=state, confidence=round(confidence, 4))

                except Exception as exc:
                    logger.error("regime_detection_failed", instrument=instrument, error=str(exc))

        if regime_snapshot:
            # Persist to DB so /regime/current REST endpoint has data on page load
            from anchor.database.models import RegimeHistory
            from anchor.utils.time_utils import utcnow as _utcnow
            _now = _utcnow()
            async with _db_engine.AsyncSessionFactory() as rh_session:
                for _inst, _snap in regime_snapshot.items():
                    rh_session.add(RegimeHistory(
                        time=_now,
                        instrument=_inst,
                        regime=_snap["state"],
                        confidence=_snap["confidence"],
                    ))
                await rh_session.commit()

            # Publish to Redis → WebSocket fanout so dashboard updates in real time
            await redis_client.publish("regime", json.dumps({
                "channel": "regime",
                "data": regime_snapshot,
            }))

        await redis_client.aclose()
        logger.info("regime_detection_complete", instruments=list(regime_snapshot.keys()))

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("regime_detection_task_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="anchor.scheduler.jobs.close_stale_trades", bind=True, max_retries=2)
def close_stale_trades(self):
    """Close open trades that have not reached 50% of TP distance within 12 hours.

    A trade drifting sideways for 12 hours has failed its thesis. Holding it
    ties up capital and a correlation slot. Close it at market to free both.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.database.engine import init_db
        from anchor.database.repositories.positions import PositionRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.utils.time_utils import utcnow

        await init_db()
        import anchor.database.engine as _db_engine

        broker = BrokerClient()
        now = utcnow()
        stale_threshold = timedelta(hours=12)

        async with _db_engine.AsyncSessionFactory() as session:
            repo = PositionRepository(session)
            open_positions = await repo.get_open()

            for pos in open_positions:
                age = now - pos.opened_at
                if age < stale_threshold:
                    continue

                if pos.take_profit is None or pos.stop_loss is None:
                    continue

                entry = float(pos.avg_entry_price)
                tp    = float(pos.take_profit)
                price = float(pos.current_price)
                tp_distance = abs(tp - entry)

                if pos.direction == "LONG":
                    progress = (price - entry) / tp_distance if tp_distance > 0 else 0
                else:
                    progress = (entry - price) / tp_distance if tp_distance > 0 else 0

                if progress < 0.5:
                    logger.info(
                        "closing_stale_trade",
                        instrument=pos.instrument,
                        direction=pos.direction,
                        age_hours=round(age.total_seconds() / 3600, 1),
                        tp_progress=round(progress, 2),
                        oanda_trade_id=pos.oanda_trade_id,
                    )
                    try:
                        await broker.close_trade(pos.oanda_trade_id)
                        await repo.mark_closed(pos.id, price, now)
                    except Exception as exc:
                        logger.error("stale_close_failed", trade_id=pos.oanda_trade_id, error=str(exc))

            await session.commit()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("close_stale_trades_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="anchor.scheduler.jobs.update_vix", bind=True, max_retries=3)
def update_vix(self):
    """Fetch latest VIX from FRED and cache in Redis.

    Key: vix_latest  TTL: 4 hours.
    VIX is daily — fetching every 4 h ensures the morning's value is available
    throughout the London/NY session. Fail-open: returns 1.0 multiplier on miss.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.signals.vix_filter import fetch_and_store
        import redis.asyncio as aioredis

        if not settings.fred_api_key:
            logger.warning("fred_api_key_not_set_for_vix")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            vix = await fetch_and_store(redis_client, settings.fred_api_key)
            logger.info("vix_updated", vix=vix)
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_vix_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=3_600)


@celery_app.task(name="anchor.scheduler.jobs.update_oanda_sentiment", bind=True, max_retries=3)
def update_oanda_sentiment(self):
    """Fetch OANDA positionBook for all instruments and cache in Redis.

    Key: oanda_sentiment:{instrument}  TTL: 5 minutes per instrument.
    Run every 5 minutes during market hours so signal scan always has fresh
    sentiment data. Fail-open: engine returns 0.5 (neutral) on cache miss.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.signals.oanda_sentiment import fetch_and_store
        import redis.asyncio as aioredis

        if not settings.oanda_api_key:
            logger.warning("oanda_api_key_not_set_for_sentiment")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            for instrument in settings.instruments:
                try:
                    long_pct = await fetch_and_store(
                        redis_client,
                        instrument,
                        settings.oanda_api_key,
                        settings.oanda_base_url,
                    )
                    logger.debug("oanda_sentiment_updated", instrument=instrument, long_pct=long_pct)
                except Exception as exc:
                    logger.warning("oanda_sentiment_instrument_failed", instrument=instrument, error=str(exc))
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_oanda_sentiment_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="anchor.scheduler.jobs.update_order_book", bind=True, max_retries=3)
def update_order_book(self):
    """Fetch OANDA orderBook for all instruments and cache in Redis.

    Key: order_book:{instrument}  TTL: 5 minutes per instrument.
    Identifies where pending orders (stops + limits) cluster near current price.
    Runs every 5 minutes, aligned with signal scan. Fail-open: 0.5 on miss.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.signals.order_book_signal import fetch_and_store
        import redis.asyncio as aioredis

        if not settings.oanda_api_key:
            logger.warning("oanda_api_key_not_set_for_order_book")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            for instrument in settings.instruments:
                try:
                    await fetch_and_store(
                        redis_client,
                        instrument,
                        settings.oanda_api_key,
                        settings.oanda_base_url,
                    )
                except Exception as exc:
                    logger.warning("order_book_instrument_failed", instrument=instrument, error=str(exc))
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_order_book_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="anchor.scheduler.jobs.update_cme_flow", bind=True, max_retries=2)
def update_cme_flow(self):
    """Fetch CME FX futures OI + volume data and cache in Redis.

    Key: cme_flow:{instrument}  TTL: 2 hours per instrument.
    Uses yfinance to pull 6E/6B/6J daily data. Runs every 2 hours.
    Fail-open: 0.5 on miss. Requires yfinance to be installed.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.data.cme_flow import fetch_and_store, _CME_MAP
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            for instrument in settings.instruments:
                if instrument not in _CME_MAP:
                    continue
                try:
                    result = await fetch_and_store(redis_client, instrument)
                    logger.debug("cme_flow_updated", instrument=instrument,
                                 raw_score=result.get("raw_score") if result else None)
                except Exception as exc:
                    logger.warning("cme_flow_instrument_failed", instrument=instrument, error=str(exc))
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_cme_flow_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=1_800)


@celery_app.task(name="anchor.scheduler.jobs.update_fx_options", bind=True, max_retries=2)
def update_fx_options(self):
    """Fetch FX ETF options chain and compute risk-reversal proxy, cache in Redis.

    Key: fx_options_rr:{instrument}  TTL: 4 hours per instrument.
    Uses yfinance options on FXE/FXB/FXY. Runs every 4 hours.
    Fail-open: 0.5 on miss. Requires yfinance to be installed.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.data.fx_options import fetch_and_store, _ETF_MAP
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            for instrument in settings.instruments:
                if instrument not in _ETF_MAP:
                    continue
                try:
                    result = await fetch_and_store(redis_client, instrument)
                    logger.debug("fx_options_rr_updated", instrument=instrument,
                                 raw_score=result.get("raw_score") if result else None)
                except Exception as exc:
                    logger.warning("fx_options_instrument_failed", instrument=instrument, error=str(exc))
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_fx_options_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=3_600)


@celery_app.task(name="anchor.scheduler.jobs.update_cross_asset", bind=True, max_retries=2)
def update_cross_asset(self):
    """Fetch SPY + GLD via yfinance, compute risk-on/off sentiment, cache in Redis.

    Key: cross_asset_risk  TTL: 2 hours.
    Drives the cross_asset confluence component for AUD, NZD, CAD, EUR_JPY.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.data.cross_asset import fetch_and_store
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            result = await fetch_and_store(redis_client)
            logger.info("cross_asset_updated",
                        regime=result.get("regime") if result else None,
                        sentiment=result.get("risk_sentiment") if result else None)
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_cross_asset_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=1_800)


@celery_app.task(name="anchor.scheduler.jobs.startup_diagnostics", bind=True, max_retries=1)
def startup_diagnostics(self):
    """Log system readiness at startup: candle counts, model files, session, account state.

    Runs once on boot (via beat schedule) so you can immediately see what's missing
    before the first signal scan fires.
    """
    async def _inner():
        from pathlib import Path
        from anchor.config import settings
        from anchor.database.engine import init_db
        from anchor.execution.broker_client import BrokerClient
        from anchor.utils.time_utils import utcnow, get_session_name
        from anchor.signals.session_filter import check_session

        await init_db()
        import anchor.database.engine as _db_engine
        from anchor.database.repositories.market_data import MarketDataRepository

        now = utcnow()
        session_ok, session_reason = check_session(now)

        # ── Account state ──────────────────────────────────────────────────
        account_info = {}
        try:
            broker = BrokerClient()
            acc = await broker.get_account_summary()
            account_info = {
                "balance":   float(acc.get("balance", 0)),
                "equity":    float(acc.get("NAV", 0)),
                "currency":  acc.get("currency", "?"),
                "open_trade_count": acc.get("openTradeCount", 0),
            }
        except Exception as exc:
            account_info = {"error": str(exc)}

        # ── Candle counts per instrument/timeframe ─────────────────────────
        candle_counts = {}
        async with _db_engine.AsyncSessionFactory() as session:
            repo = MarketDataRepository(session)
            for inst in settings.instruments:
                candle_counts[inst] = {}
                for tf in ("H1", "H4", "D"):
                    try:
                        rows = await repo.get_latest_n_candles(inst, tf, 300)
                        candle_counts[inst][tf] = len(rows)
                    except Exception:
                        candle_counts[inst][tf] = "ERROR"

        # ── Model files ────────────────────────────────────────────────────
        model_dir = Path("/app/models")
        model_status = {}
        for inst in settings.instruments:
            model_status[inst] = {
                "xgb":  (model_dir / f"{inst}_xgb.pkl").exists(),
                "ood":  (model_dir / f"{inst}_ood.pkl").exists(),
                "hmm":  (model_dir / f"hmm_{inst}.pkl").exists(),
            }
        model_status["hmm_latest"] = (model_dir / "hmm_latest.pkl").exists()

        # ── Redis connectivity ─────────────────────────────────────────────
        redis_ok = False
        try:
            import redis.asyncio as aioredis
            rc = aioredis.from_url(settings.redis_url, decode_responses=True)
            await rc.ping()
            redis_ok = True
            await rc.aclose()
        except Exception:
            redis_ok = False

        logger.info(
            "startup_diagnostics",
            utc_time=now.isoformat(),
            session=get_session_name(now),
            session_active=session_ok,
            session_reason=session_reason,
            account=account_info,
            candle_counts=candle_counts,
            models=model_status,
            redis_ok=redis_ok,
            oanda_key_set=bool(settings.oanda_api_key),
            fred_key_set=bool(settings.fred_api_key),
        )

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("startup_diagnostics_failed", error=str(exc))


@celery_app.task(name="anchor.scheduler.jobs.run_partial_tp", bind=True, max_retries=2)
def run_partial_tp(self):
    """Check all open positions and execute partial TP where the 1×ATR trigger has been hit.

    Runs every 5 minutes. At 1×ATR profit:
      - Close 50% of position at market
      - Move SL to breakeven on remaining 50%
      - Let remainder run to original TP
    """
    async def _inner():
        from anchor.database.engine import init_db
        from anchor.database.repositories.positions import PositionRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.execution.partial_tp_manager import PartialTPManager

        await init_db()
        import anchor.database.engine as _db_engine

        broker = BrokerClient()

        async with _db_engine.AsyncSessionFactory() as session:
            pos_repo = PositionRepository(session)
            manager  = PartialTPManager(
                broker_client=broker,
                position_repo=pos_repo,
                alerts=_alerts,
            )
            executed = await manager.run()
            await session.commit()

        if executed:
            logger.info("partial_tp_cycle_complete", partial_closes=executed)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("run_partial_tp_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="anchor.scheduler.jobs.retrain_models", bind=True, max_retries=1)
def retrain_models(self, instrument: Optional[str] = None):
    """Monthly ML retraining for all instruments."""
    async def _inner():
        from anchor.ml.retraining import run_retraining
        results = await run_retraining(instrument)
        logger.info("retraining_complete", results=results)
        return results

    try:
        return _run_async(_inner())
    except Exception as exc:
        logger.error("retraining_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=3600)


# ── fit_weights trigger thresholds ───────────────────────────────────────────
# First trigger at 200 trades (minimum for reliable L1 regression).
# Re-triggers every 200 trades after that (400, 600, 800, ...).
# Does NOT auto-apply weights — sends Telegram alert with results for review.
_FW_FIRST_THRESHOLD = 200
_FW_RETRIGGER_EVERY = 200


@celery_app.task(name="anchor.scheduler.jobs.check_fit_weights_trigger", bind=True, max_retries=1)
def check_fit_weights_trigger(self):
    """
    Daily check: when closed live trades cross 200 (then every 200 after),
    run the L1 weight regression analysis and send a Telegram alert with
    the results. Does NOT auto-apply — the user reviews and runs
    `make fit-weights-live --apply` to accept the new weights.

    Trigger logic:
      - Count closed trades with signal_id in DB (true OOS trades only).
      - Read last trigger count from system_events (FW_TRIGGER_RAN event).
      - Fire when: count >= 200 AND count // 200 > last_count // 200.
      - Always write FW_TRIGGER_CHECKED event with current count (progress log).
    """
    import json
    from sqlalchemy import create_engine, text
    from anchor.config import get_settings

    cfg = get_settings()
    db  = create_engine(cfg.sync_database_url)

    # ── Step 1: count closed OOS trades ──────────────────────────────────────
    with db.connect() as conn:
        trade_count = conn.execute(text(
            "SELECT COUNT(*) FROM trades "
            "WHERE closed_at IS NOT NULL AND signal_id IS NOT NULL"
        )).scalar() or 0

        # Last count at which the regression was run
        row = conn.execute(text(
            "SELECT metadata FROM system_events "
            "WHERE event_type = 'FW_TRIGGER_RAN' "
            "ORDER BY event_at DESC LIMIT 1"
        )).fetchone()

    last_trigger_count = 0
    if row and row[0]:
        try:
            last_trigger_count = int(row[0].get("trade_count", 0))
        except (TypeError, AttributeError, ValueError):
            last_trigger_count = 0

    logger.info(
        "fw_trigger_checked",
        trade_count=trade_count,
        last_trigger_count=last_trigger_count,
        threshold=_FW_FIRST_THRESHOLD,
    )

    # ── Step 2: write progress event (always — creates visible log) ──────────
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'FW_TRIGGER_CHECKED', 'INFO', 'fit_weights',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "msg":  f"Trade count: {trade_count} / next trigger at "
                    f"{((trade_count // _FW_RETRIGGER_EVERY) + 1) * _FW_RETRIGGER_EVERY}",
            "meta": json.dumps({
                "trade_count":       trade_count,
                "last_trigger_count": last_trigger_count,
                "next_trigger":      (
                    (trade_count // _FW_RETRIGGER_EVERY) + 1
                ) * _FW_RETRIGGER_EVERY,
            }),
        })

    # ── Step 3: decide whether to trigger ────────────────────────────────────
    if trade_count < _FW_FIRST_THRESHOLD:
        return  # not enough trades yet

    current_band = trade_count // _FW_RETRIGGER_EVERY
    last_band    = last_trigger_count // _FW_RETRIGGER_EVERY
    if current_band <= last_band:
        return  # already ran at this band

    # ── Step 4: run regression analysis ──────────────────────────────────────
    logger.info("fw_trigger_firing", trade_count=trade_count)

    try:
        import warnings
        import logging as _logging
        _logging.disable(_logging.CRITICAL)
        warnings.filterwarnings("ignore")

        import pandas as pd
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        from anchor.backtesting.fit_weights import (
            _collect_live_trades, _fit, CURRENT_WEIGHTS, COMPONENT_MAP,
        )

        df = _collect_live_trades()
        if len(df) < 50:
            logger.warning("fw_trigger_too_few_trades", count=len(df))
            return

        new_weights = _fit(df, C=1.0)

        # ── Auto-apply: patch engine.py + fit_weights.py + write audit event ─
        from anchor.backtesting.fit_weights import (
            _patch_engine, _patch_current_weights, _log_system_event,
        )
        from pathlib import Path
        _patch_engine(new_weights, Path("/app/anchor/signals/engine.py"))
        _patch_current_weights(new_weights, Path("/app/anchor/backtesting/fit_weights.py"))
        _log_system_event(new_weights, "live_trades_auto", len(df))

        logger.info("fw_weights_applied", trade_count=trade_count, new_weights=new_weights)

        # Build Telegram summary
        wins = int((df["outcome"] == 1).sum())
        lines = [
            "*Weights Auto-Updated*",
            f"Trades: {len(df)}  WR: {wins/len(df)*100:.1f}%",
            "",
            "```",
            f"{'Component':<22} {'Old':>7} {'New':>7} {'Delta':>7}",
            "-" * 48,
        ]
        for k, old_v in CURRENT_WEIGHTS.items():
            new_v = new_weights.get(k, 0.0)
            delta = new_v - old_v
            sign  = "+" if delta >= 0 else ""
            lines.append(f"{k:<22} {old_v:.4f}  {new_v:.4f}  {sign}{delta:.4f}")
        lines.append("```")
        lines.append("engine.py patched and reloads on next signal scan.")

        alert_msg = "\n".join(lines)

    except Exception as exc:
        logger.error("fw_trigger_regression_failed", error=str(exc))
        alert_msg = (
            f"*Weight Optimizer Failed* — {trade_count} trades in DB\n"
            f"Error: {exc}\n"
            f"Run manually: `make fit-weights-live-apply`"
        )
        new_weights = {}

    # ── Step 5: send Telegram alert ───────────────────────────────────────────
    try:
        _run_async(_alerts.send(alert_msg))
    except Exception as exc:
        logger.warning("fw_trigger_alert_failed", error=str(exc))

    # ── Step 6: write FW_TRIGGER_RAN event (prevents re-firing this band) ────
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'FW_TRIGGER_RAN', 'INFO', 'fit_weights',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "msg":  f"Regression run at {trade_count} trades",
            "meta": json.dumps({
                "trade_count":  trade_count,
                "new_weights":  new_weights,
                "old_weights":  CURRENT_WEIGHTS,
            }),
        })


# ── Live performance monitor ───────────────────────────────────────────────────

# Backtest benchmarks (8-year OOS validation results).
# Alert when rolling live metrics fall this far below benchmark.
_PERF_BENCH = {
    "LCR":    {"wr": 0.44, "pf": 1.40},   # worst qualifying LCR pair (GBP_USD)
    "LONDON": {"wr": 0.50, "pf": 1.20},   # London Trend OOS result
}
_PERF_WR_MARGIN    = 0.08   # 8pp below benchmark WR → warning
_PERF_MIN_TRADES   = 20     # minimum closed trades before comparing
_PERF_WIN_DROUGHT  = 30     # days since last win → alert


@celery_app.task(name="anchor.scheduler.jobs.monitor_live_performance", bind=True, max_retries=1)
def monitor_live_performance(self):
    """
    Daily check: compare actual live trade performance vs backtested benchmarks.

    Computes rolling WR and PF over the last 50 closed trades per strategy.
    Alerts via Telegram if actual performance degrades below OOS expectations.

    Alert triggers:
      - Rolling WR < backtest_WR - 8pp  (strategy losing its edge)
      - Rolling PF < 1.0               (strategy actively losing money)
      - 30+ days since last winning trade (win drought)
    """
    import json
    from sqlalchemy import create_engine, text as _text
    import pandas as _pd
    from anchor.config import get_settings

    cfg = get_settings()
    db  = create_engine(cfg.sync_database_url)

    with db.connect() as conn:
        rows = conn.execute(_text("""
            SELECT
                t.closed_at,
                t.net_pl,
                t.pl_pct,
                t.instrument,
                s.session,
                s.confluence_score,
                t.signal_id
            FROM trades t
            LEFT JOIN signals s ON s.id = t.signal_id
            WHERE t.closed_at IS NOT NULL
            ORDER BY t.closed_at DESC
            LIMIT 200
        """)).fetchall()

    if not rows:
        logger.info("live_perf_monitor_skip", reason="no_closed_trades")
        return

    df = _pd.DataFrame(
        rows,
        columns=["closed_at", "net_pl", "pl_pct", "instrument",
                 "session", "confluence_score", "signal_id"],
    )
    df["closed_at"] = _pd.to_datetime(df["closed_at"], utc=True)
    df["is_win"]    = df["pl_pct"] > 0

    # Split by strategy via session tag
    lcr_df    = df[df["session"] == "NY_LCR"].head(50)
    london_df = df[df["session"].isin(["LONDON"])].head(50)

    alerts: list[str]    = []
    summaries: list[dict] = []

    for label, bench, strat_df in [
        ("LCR",    _PERF_BENCH["LCR"],    lcr_df),
        ("LONDON", _PERF_BENCH["LONDON"], london_df),
    ]:
        n = len(strat_df)
        if n < _PERF_MIN_TRADES:
            summaries.append({"strategy": label, "n": n, "status": "insufficient_trades"})
            continue

        wr = float(strat_df["is_win"].mean())
        wins   = strat_df[strat_df["is_win"]]
        losses = strat_df[~strat_df["is_win"]]
        gross_wins   = float(wins["pl_pct"].sum())
        gross_losses = abs(float(losses["pl_pct"].sum()))
        pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")

        # Days since last win
        last_win_ts = strat_df[strat_df["is_win"]]["closed_at"].max()
        neg_streak_days = 0
        if _pd.notna(last_win_ts):
            neg_streak_days = (_pd.Timestamp.utcnow() - last_win_ts).days

        summary: dict = {
            "strategy":         label,
            "n_trades":         n,
            "rolling_wr_pct":   round(wr * 100, 1),
            "bench_wr_pct":     round(bench["wr"] * 100, 1),
            "rolling_pf":       round(pf, 3) if pf != float("inf") else 9.999,
            "bench_pf":         bench["pf"],
            "days_since_win":   neg_streak_days,
            "status":           "ok",
        }

        if wr < (bench["wr"] - _PERF_WR_MARGIN):
            gap = (bench["wr"] - wr) * 100
            alerts.append(
                f"{label}: rolling WR {wr*100:.1f}% is {gap:.1f}pp below "
                f"backtest benchmark {bench['wr']*100:.0f}% (last {n} trades)"
            )
            summary["status"] = "DEGRADED_WR"

        if pf < 1.0:
            alerts.append(
                f"{label}: rolling PF {pf:.3f} < 1.0 (last {n} trades) — "
                f"strategy is net-negative, review immediately"
            )
            summary["status"] = "UNPROFITABLE"

        if neg_streak_days >= _PERF_WIN_DROUGHT:
            alerts.append(
                f"{label}: {neg_streak_days} days since last winning trade — "
                f"possible regime change, consider pausing"
            )
            summary["status"] = "WIN_DROUGHT"

        summaries.append(summary)
        logger.info("live_perf_monitor", **summary)

    # Write to system_events
    severity = "WARNING" if alerts else "INFO"
    message  = "; ".join(alerts) if alerts else "Live performance within benchmarks"
    with db.begin() as conn:
        conn.execute(_text("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'LIVE_PERF_CHECK', :sev, 'monitor_live_performance',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "sev":  severity,
            "msg":  message,
            "meta": json.dumps({"summaries": summaries, "alerts": alerts}),
        })

    # Telegram alert if degrading
    if alerts:
        try:
            alert_text = (
                "*ANCHOR PERFORMANCE ALERT*\n\n"
                + "\n".join(f"• {a}" for a in alerts)
                + "\n\nCheck `make fit-weights-progress` and review recent trades."
            )
            _run_async(_alerts.send(alert_text))
        except Exception as exc:
            logger.warning("live_perf_alert_failed", error=str(exc))


# ── Edge confidence monitor ────────────────────────────────────────────────────

@celery_app.task(name="anchor.scheduler.jobs.assess_edge_confidence", bind=True, max_retries=1)
def assess_edge_confidence(self):
    """
    Daily assessment of whether the macro environment supports Anchor's
    session-based structural edges.

    Runs three signals:
      1. Session character  — London session trendiness vs choppiness
      2. Pair correlation   — breakdown between normally-correlated pairs
      3. Macro stress       — VIX acceleration + economic surprise extremes

    Output: EDGE_CONFIDENCE_CHECK system event + Telegram alert on state change
    or when confidence is REDUCED/LOW.

    Runs daily after London close (18:00 UTC). Not a trading decision —
    a signal to the human operator to review the environment.
    """
    import json as _json
    from sqlalchemy import create_engine as _ce, text as _t
    import redis.asyncio as _redis_async
    from anchor.config import get_settings
    from anchor.database.engine import AsyncSessionLocal
    from anchor.monitoring.edge_confidence import assess_edge_confidence as _assess, build_alert_message

    cfg = get_settings()
    db  = _ce(cfg.sync_database_url)

    # ── Load previous confidence level ────────────────────────────────────────
    previous_confidence: str | None = None
    with db.connect() as conn:
        row = conn.execute(_t("""
            SELECT metadata FROM system_events
            WHERE event_type = 'EDGE_CONFIDENCE_CHECK'
            ORDER BY event_at DESC LIMIT 1
        """)).fetchone()
        if row and row[0]:
            try:
                previous_confidence = row[0].get("confidence")
            except (AttributeError, TypeError):
                pass

    # ── Run assessment ────────────────────────────────────────────────────────
    async def _run():
        redis_client = _redis_async.from_url(cfg.redis_url, decode_responses=True)
        try:
            async with AsyncSessionLocal() as db_session:
                return await _assess(db_session, redis_client, previous_confidence)
        finally:
            await redis_client.aclose()

    result = _run_async(_run())

    # ── Write system event ────────────────────────────────────────────────────
    severity = {"HIGH": "INFO", "REDUCED": "WARNING", "LOW": "CRITICAL"}.get(result.confidence, "INFO")
    with db.begin() as conn:
        conn.execute(_t("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'EDGE_CONFIDENCE_CHECK', :sev, 'edge_confidence',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "sev":  severity,
            "msg":  f"Edge confidence: {result.confidence} ({result.flag_count} flag(s))",
            "meta": _json.dumps(result.to_dict()),
        })

    logger.info(
        "edge_confidence_assessed",
        confidence=result.confidence,
        flags=result.flag_count,
        previous=previous_confidence,
    )

    # ── Alert on state change or non-HIGH confidence ──────────────────────────
    state_changed   = previous_confidence and previous_confidence != result.confidence
    is_degraded     = result.confidence in ("REDUCED", "LOW")
    should_alert    = state_changed or is_degraded

    if should_alert:
        try:
            msg = build_alert_message(result)
            if result.confidence == "LOW":
                _run_async(_alerts.send_critical(msg))
            elif result.confidence == "REDUCED":
                _run_async(_alerts.send_warning(msg))
            else:
                _run_async(_alerts.send_info(msg))
        except Exception as exc:
            logger.warning("edge_confidence_alert_failed", error=str(exc))


# ── AI Intelligence Layer ──────────────────────────────────────────────────────

def _run_intelligence_brief(report_type: str) -> None:
    """Shared async runner for all three brief types."""
    async def _inner():
        import redis.asyncio as aioredis
        from anchor.config import settings
        from anchor.database.engine import init_db, get_session
        from anchor.database.models import IntelligenceReport
        from anchor.intelligence.context_builder import build_context
        from anchor.intelligence.report_generator import (
            generate_presession_brief,
            generate_postsession_debrief,
            generate_weekly_synthesis,
        )

        if not settings.anthropic_api_key:
            logger.warning("intelligence_skipped_no_api_key", report_type=report_type)
            return

        await init_db()
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

        try:
            async with get_session() as session:
                ctx = await build_context(report_type, session, redis_client)

                if report_type == "PRESESSION":
                    content, tokens = await generate_presession_brief(ctx)
                    # Tier 1: generate structured session quality alongside the brief
                    # Written to Redis 'session_quality' (TTL 8h) so engine + sizer can read it
                    try:
                        import json as _json
                        from anchor.intelligence.session_quality import (
                            generate_session_quality,
                            _REDIS_KEY as _SQ_KEY,
                            _TTL_SECONDS as _SQ_TTL,
                        )
                        sq = await generate_session_quality(ctx)
                        await redis_client.set(_SQ_KEY, _json.dumps(sq), ex=_SQ_TTL)
                        logger.info(
                            "session_quality_cached",
                            environment=sq["environment"],
                            confidence=sq["confidence"],
                            size_scale=sq["size_scale"],
                            threshold_adj=sq["threshold_adjustment"],
                        )
                    except Exception as _sq_exc:
                        logger.warning("session_quality_write_failed", error=str(_sq_exc))
                elif report_type == "POSTSESSION":
                    content, tokens = await generate_postsession_debrief(ctx)
                    # Tier 2b: rolling session score (0–10 edge quality, last 5 sessions)
                    try:
                        from anchor.intelligence.report_generator import score_postsession as _score_ps
                        _score = await _score_ps(content)
                        await redis_client.lpush("rolling_session_scores", _score)
                        await redis_client.ltrim("rolling_session_scores", 0, 4)
                        _scores_raw = await redis_client.lrange("rolling_session_scores", 0, -1)
                        _scores = [float(s) for s in _scores_raw]
                        _rolling_avg = sum(_scores) / len(_scores) if _scores else 5.0
                        await redis_client.set("rolling_score_avg", _rolling_avg, ex=7 * 86_400)
                        logger.info(
                            "rolling_session_score_updated",
                            score=_score,
                            rolling_avg=round(_rolling_avg, 2),
                            n=len(_scores),
                        )
                    except Exception as _rs_exc:
                        logger.warning("rolling_session_score_failed", error=str(_rs_exc))
                    # Tier 2c: macro anomaly detection — flags price vs macro dissonance per pair
                    try:
                        from anchor.intelligence.anomaly_detector import detect_macro_anomaly
                        _dissonant = await detect_macro_anomaly(ctx, redis_client)
                        if _dissonant:
                            logger.info("macro_anomaly_pairs_flagged", pairs=_dissonant)
                    except Exception as _ma_exc:
                        logger.warning("macro_anomaly_task_failed", error=str(_ma_exc))
                else:
                    content, tokens = await generate_weekly_synthesis(ctx)
                    # Tier 2b: reset rolling scores on weekly synthesis (fresh week slate)
                    try:
                        await redis_client.delete("rolling_session_scores", "rolling_score_avg")
                        logger.info("rolling_session_scores_reset")
                    except Exception as _rs_exc:
                        logger.warning("rolling_session_scores_reset_failed", error=str(_rs_exc))

                report = IntelligenceReport(
                    report_type=report_type,
                    content=content,
                    context_snapshot=ctx,
                    tokens_used=tokens,
                )
                session.add(report)
                await session.commit()
                await session.refresh(report)

            # Deliver via Telegram (truncate at 4096 chars)
            header = {
                "PRESESSION": "📊 PRE-SESSION BRIEF",
                "POSTSESSION": "📋 POST-SESSION DEBRIEF",
                "WEEKLY": "📈 WEEKLY SYNTHESIS",
            }[report_type]
            await _alerts.send_info(f"<b>{header}</b>\n\n{content[:3900]}")

            # Mark delivered
            async with get_session() as session:
                r = await session.get(IntelligenceReport, report.id)
                if r:
                    r.delivered_telegram = True
                    await session.commit()

            logger.info("intelligence_brief_complete", report_type=report_type, tokens=tokens)

        except Exception as exc:
            logger.error("intelligence_brief_failed", report_type=report_type, error=str(exc))
        finally:
            await redis_client.aclose()

    _run_async(_inner())


@celery_app.task(name="anchor.scheduler.jobs.generate_presession_brief", bind=True, max_retries=2)
def generate_presession_brief(self):
    """Generate and deliver the London pre-session brief at 06:30 UTC (Mon–Fri)."""
    try:
        _run_intelligence_brief("PRESESSION")
    except Exception as exc:
        logger.error("presession_brief_task_error", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.generate_postsession_debrief", bind=True, max_retries=2)
def generate_postsession_debrief(self):
    """Generate and deliver the post-London-session debrief at 12:30 UTC (Mon–Fri)."""
    try:
        _run_intelligence_brief("POSTSESSION")
    except Exception as exc:
        logger.error("postsession_debrief_task_error", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.generate_weekly_synthesis", bind=True, max_retries=2)
def generate_weekly_synthesis(self):
    """Generate and deliver the weekly synthesis at 22:00 UTC on Sundays."""
    try:
        _run_intelligence_brief("WEEKLY")
    except Exception as exc:
        logger.error("weekly_synthesis_task_error", error=str(exc))
        raise self.retry(exc=exc, countdown=600)


@celery_app.task(name="anchor.scheduler.jobs.run_intrabar_anomaly_check", bind=True, max_retries=1)
def run_intrabar_anomaly_check(self):
    """
    Hourly intrabar macro dissonance check during London session (07:05–11:05 UTC Mon–Fri).

    Fetches the last 4 H1 bars per pair + macro snapshot from Redis, then asks
    Claude Haiku whether current price action contradicts the macro backdrop.
    Flags are written as macro_dissonance:{pair} with a 2-hour TTL — the same
    Redis key the signal engine already reads for the -0.05 confluence penalty.
    The postsession check at 12:30 UTC owns clearing stale flags.
    """
    import json as _json
    from datetime import datetime, timezone as _tz
    import redis.asyncio as _redis_async
    from anchor.config import get_settings as _get_settings
    from anchor.database.engine import AsyncSessionLocal
    from anchor.database.repositories.market_data import MarketDataRepository
    from anchor.intelligence.anomaly_detector import detect_intrabar_anomaly as _detect

    cfg = _get_settings()

    async def _inner():
        now_utc = datetime.now(_tz.utc)
        if not (7 <= now_utc.hour < 12):
            logger.info("intrabar_anomaly_skipped_outside_london", hour=now_utc.hour)
            return

        redis_client = _redis_async.from_url(cfg.redis_url, decode_responses=True)
        try:
            # ── Macro snapshot from Redis ─────────────────────────────────────
            async def _rget(key: str):
                try:
                    raw = await redis_client.get(key)
                    return _json.loads(raw) if raw else None
                except Exception:
                    return None

            macro: dict = {}
            for rkey, label in [
                ("vix_data",         "vix"),
                ("fred_rate_diff",   "rate_differentials"),
                ("cross_asset_risk", "cross_asset"),
                ("cot_data",         "cot_positioning"),
            ]:
                val = await _rget(rkey)
                if val is not None:
                    macro[label] = val

            surprises = {}
            for ccy in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD"]:
                val = await _rget(f"econ_surprise:{ccy}")
                if val is not None:
                    surprises[ccy] = val
            if surprises:
                macro["economic_surprise"] = surprises

            # ── Last 4 H1 bars per pair ───────────────────────────────────────
            pairs_bars: dict = {}
            async with AsyncSessionLocal() as session:
                repo = MarketDataRepository(session)
                for pair in cfg.instruments:
                    candles = await repo.get_latest_n_candles(pair, "H1", 4)
                    pairs_bars[pair] = [
                        {
                            "time":  str(c.time),
                            "open":  float(c.open),
                            "high":  float(c.high),
                            "low":   float(c.low),
                            "close": float(c.close),
                        }
                        for c in candles
                    ]

            if not any(pairs_bars.values()):
                logger.warning("intrabar_anomaly_no_candle_data")
                return

            dissonant = await _detect(pairs_bars, macro, redis_client)

            if dissonant:
                msg = (
                    f"\u26a0\ufe0f Intrabar dissonance {now_utc.strftime('%H:%M')} UTC\n"
                    f"Pairs: {', '.join(dissonant)}\n"
                    f"Confluence penalty active — new entries suppressed on flagged pairs."
                )
                await _alerts.send_warning(msg)

        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("intrabar_anomaly_check_task_error", error=str(exc))
        raise self.retry(exc=exc, countdown=120)
