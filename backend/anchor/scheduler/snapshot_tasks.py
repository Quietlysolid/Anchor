"""Snapshot and diagnostics tasks: equity curve, regime detection, startup check."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async, _alerts

logger = structlog.get_logger(__name__)


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
