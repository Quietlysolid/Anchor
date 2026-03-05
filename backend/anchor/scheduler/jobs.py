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


@celery_app.task(name="anchor.scheduler.jobs.update_cot_data", bind=True, max_retries=2)
def update_cot_data(self):
    """Download and store latest CFTC COT report."""
    async def _inner():
        from anchor.data.cot_parser import CotParser

        async with CotParser() as parser:
            result = await parser.fetch_latest()
        logger.info("cot_updated", currencies=list(result.keys()))
        # Store in Redis for quick access by signal engine
        # (full DB storage would require a dedicated table — keep in Redis for now)

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
        import redis.asyncio as aioredis

        await init_db()
        import anchor.database.engine as _db_engine
        now = utcnow()

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

        news_filter = NewsFilter(calendar_repo=None)
        engine = ConfluenceEngine(news_filter=news_filter)

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

        # Evaluate + persist each instrument in its own isolated session
        # so one DB error doesn't poison the others
        for instrument in settings.instruments:
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

                    # 5. Compute ATR-based stop loss & take profit
                    h1_df = to_df(h1)
                    atr = (h1_df["high"] - h1_df["low"]).rolling(14).mean().iloc[-1]
                    entry = float(h1_df["close"].iloc[-1])
                    pip_size = get_pip_size(instrument)

                    if result.direction == "LONG":
                        stop_loss   = round(entry - 1.5 * atr, 5)
                        take_profit = round(entry + 3.0 * atr, 5)
                    else:
                        stop_loss   = round(entry + 1.5 * atr, 5)
                        take_profit = round(entry - 3.0 * atr, 5)

                    # 6. Size the position
                    units = sizer.compute(
                        account_balance=balance,
                        instrument=instrument,
                        entry_price=entry,
                        stop_loss=stop_loss,
                        kelly_fraction=float(result.ml_confidence) if result.ml_confidence else None,
                        drawdown_scale=drawdown_monitor.scale_factor,
                    )

                    # 7. Submit order
                    direction = Direction.LONG if result.direction == "LONG" else Direction.SHORT
                    order_request = OrderRequest(
                        instrument=instrument,
                        direction=direction,
                        units=units,
                        order_type=OrderType.MARKET,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        signal_id=row.id,
                    )

                    order_id = await order_manager.submit(order_request)
                    logger.info(
                        "trade_submitted",
                        instrument=instrument,
                        direction=result.direction,
                        units=units,
                        entry=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        confluence=result.confluence_score,
                        order_id=str(order_id),
                    )
                    await session.commit()

            except Exception as exc:
                logger.error("signal_scan_instrument_failed", instrument=instrument, error=str(exc))

        await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("signal_scan_failed", error=str(exc))
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
