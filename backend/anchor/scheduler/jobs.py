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
                        vix_scale=result.vix_multiplier,  # VIX-based size reduction
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
                    already_open = any(p.instrument == instrument for p in open_positions)
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
                    already_open = any(p.instrument == instrument for p in open_positions)
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
