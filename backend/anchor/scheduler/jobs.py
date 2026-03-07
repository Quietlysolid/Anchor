"""Celery task definitions for all scheduled jobs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from anchor.scheduler.celery_app import celery_app

logger = structlog.get_logger(__name__)


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
        from anchor.database.engine import get_session
        from anchor.database.repositories import EquityRepository
        from anchor.database.models import EquityCurvePoint
        from anchor.execution.broker_client import BrokerClient

        client = BrokerClient()
        account = client.get_account_summary()
        if not account:
            logger.warning("snapshot_equity_no_account")
            return

        balance = float(account.get("balance", 0))
        equity = float(account.get("NAV", balance))
        unrealized = equity - balance

        async with get_session() as session:
            repo = EquityRepository(session)
            latest = await repo.get_latest()
            peak = max(equity, latest.peak_equity if latest else equity)
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
        from anchor.database.engine import get_session
        from anchor.database.repositories import EconomicCalendarRepository
        from datetime import date

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
        from anchor.data.fred_rates import fetch_and_store
        import redis.asyncio as aioredis

        if not settings.fred_api_key:
            logger.warning("fred_api_key_not_set")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            result = await fetch_and_store(redis_client, settings.fred_api_key)
            available = [k for k, v in result.items() if v["available"]]
            logger.info("fred_rates_updated", available=available)
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
        import pandas as pd
        from anchor.config import settings
        from anchor.database.engine import init_db
        from anchor.database.repositories.market_data import MarketDataRepository
        from anchor.database.repositories.orders import OrderRepository
        from anchor.database.repositories.positions import PositionRepository
        from anchor.database.models import Signal as SignalModel
        from anchor.signals.engine import ConfluenceEngine
        from anchor.signals.news_filter import NewsFilter
        from anchor.execution.broker_client import BrokerClient
        from anchor.execution.order_manager import OrderManager
        from anchor.execution.order_types import OrderRequest, Direction, OrderType
        from anchor.risk.position_sizer import PositionSizer
        from anchor.risk.drawdown_monitor import DrawdownMonitor
        from anchor.risk.daily_limiter import DailyLimiter
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
        drawdown_monitor = DrawdownMonitor()
        daily_limiter = DailyLimiter()
        correlation_mgr = CorrelationManager()

        # Get live account state once for the whole scan
        account = await broker.get_account_summary()
        balance = float(account.get("balance", 0))
        equity  = float(account.get("NAV", balance))
        drawdown_monitor.update(equity)

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

        def to_df(rows):
            return pd.DataFrame([{
                "time": r.time, "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close),
            } for r in rows]).set_index("time")

        # Load ML classifiers from disk (written by retrain_models task).
        # Falls back gracefully — ConfluenceEngine raises threshold +10% when absent.
        from pathlib import Path as _Path
        # Pass sync Redis to FeatureEngineer so COT features are populated at prediction time
        feature_engineer = FeatureEngineer(redis_client=sync_redis)
        ood_detector = OODDetector()
        _classifiers: dict = {}
        for _inst in settings.instruments:
            _clf = XGBDirectionClassifier()
            _model_path = _Path("/app/models") / f"{_inst}_xgb.pkl"
            if _clf.load(_model_path):
                _classifiers[_inst] = _clf
            else:
                logger.warning("ml_model_missing", instrument=_inst, path=str(_model_path))

        news_filter = NewsFilter(calendar_repo=None)

        # Build correlation matrix + open positions in a short-lived session
        async with _db_engine.AsyncSessionFactory() as session:
            market_repo   = MarketDataRepository(session)
            position_repo = PositionRepository(session)

            daily_closes = {}
            for inst in settings.instruments:
                rows = await market_repo.get_latest_n_candles(inst, "D", 50)
                if len(rows) >= 2:
                    daily_closes[inst] = pd.Series(
                        [float(r.close) for r in rows],
                        index=[r.time for r in rows],
                    )
            await correlation_mgr.update(daily_closes)
            open_positions = await position_repo.get_open()

        # Shared engine instance; ML classifier is swapped per-instrument below
        engine = ConfluenceEngine(
            news_filter=news_filter,
            feature_engineer=feature_engineer,
            ood_detector=ood_detector,
        )

        # Instruments with no confluence-only edge (backtest Sharpe < 0, WR < 30%).
        # Re-enable once XGB models are trained (~60 days of live data).
        _NO_TRADE_WITHOUT_ML = {"AUD_USD", "GBP_USD"}

        # Evaluate + persist each instrument in its own isolated session
        # so one DB error doesn't poison the others
        for instrument in settings.instruments:
            if instrument in _NO_TRADE_WITHOUT_ML and instrument not in _classifiers:
                logger.debug("signal_scan_skipped_no_ml", instrument=instrument)
                continue
            try:
                async with _db_engine.AsyncSessionFactory() as session:
                    market_repo   = MarketDataRepository(session)
                    order_repo    = OrderRepository(session)
                    order_manager = OrderManager(order_repo, broker, redis=redis_client)

                    h1 = await market_repo.get_latest_n_candles(instrument, "H1", 200)
                    h4 = await market_repo.get_latest_n_candles(instrument, "H4", 100)
                    d1 = await market_repo.get_latest_n_candles(instrument, "D", 50)

                    if len(h1) < 50:
                        logger.warning("signal_scan_insufficient_data", instrument=instrument)
                        continue

                    engine.update_cache(instrument, "H1", to_df(h1))
                    engine.update_cache(instrument, "H4", to_df(h4))
                    engine.update_cache(instrument, "D",  to_df(d1))

                    # Inject instrument-specific classifier (None → engine falls back gracefully)
                    engine.ml_classifier = _classifiers.get(instrument)
                    if not engine.ml_classifier:
                        engine.feature_engineer = None
                        engine.ood_detector = None
                    else:
                        engine.feature_engineer = feature_engineer
                        engine.ood_detector = ood_detector

                    result = await engine.evaluate(instrument, dt=now)

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
                    )
                    session.add(row)
                    await session.flush()  # get row.id assigned

                    if result.suppressed:
                        await session.commit()
                        continue

                    # ── Execution gate ────────────────────────────────────────

                    # 1. Drawdown circuit breaker
                    dd_ok, dd_reason = drawdown_monitor.check()
                    if not dd_ok:
                        logger.warning("trade_blocked_drawdown", instrument=instrument, reason=dd_reason)
                        await session.commit()
                        continue

                    # 2. Daily loss limit
                    if daily_limiter.is_halted(balance):
                        logger.warning("trade_blocked_daily_limit", instrument=instrument)
                        await session.commit()
                        continue

                    # 3. Already have an open position in this instrument?
                    already_open = any(p.instrument == instrument for p in open_positions)
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
                        take_profit = round(entry + 3.0 * atr, 5)
                    else:
                        entry       = round(close_price + pullback, 5)
                        stop_loss   = round(entry + 1.5 * atr, 5)
                        take_profit = round(entry - 3.0 * atr, 5)

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
                    await session.commit()

            except Exception as exc:
                logger.error("signal_scan_instrument_failed", instrument=instrument, error=str(exc))

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
            "H1": timedelta(days=9),   # ~200 H1 bars
            "H4": timedelta(days=17),  # ~100 H4 bars
            "D":  timedelta(days=65),  # ~60 D bars
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
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        detector = HMMRegimeDetector()
        regime_snapshot = {}

        async with _db_engine.AsyncSessionFactory() as session:
            repo = MarketDataRepository(session)
            for instrument in settings.instruments:
                try:
                    rows = await repo.get_latest_n_candles(instrument, "D", 300)
                    if len(rows) < 60:
                        continue

                    df = pd.DataFrame([{
                        "time": r.time, "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close),
                    } for r in rows]).set_index("time")

                    if not detector.is_ready:
                        detector.fit(df)

                    state, confidence = detector.predict_current(df)
                    regime_snapshot[instrument] = {"state": state, "confidence": round(confidence, 4)}

                    logger.debug("regime_detected", instrument=instrument, state=state, confidence=confidence)

                except Exception as exc:
                    logger.error("regime_detection_failed", instrument=instrument, error=str(exc))

        if regime_snapshot:
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
