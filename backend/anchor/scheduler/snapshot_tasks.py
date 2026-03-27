"""Snapshot and diagnostics tasks for the futures runtime."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async

logger = structlog.get_logger(__name__)


def _runtime_instruments() -> list[str]:
    from anchor.config import settings

    if settings.trading_domain == "futures":
        return list(settings.futures_v1_markets)
    return list(settings.instruments)


@celery_app.task(name="anchor.scheduler.jobs.snapshot_equity", bind=True, max_retries=3)
def snapshot_equity(self):
    """Write an equity curve point every 15 minutes."""

    async def _inner():
        from anchor.database.engine import get_session, init_db
        from anchor.database.models import EquityCurvePoint
        from anchor.database.repositories import EquityRepository
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


@celery_app.task(name="anchor.scheduler.jobs.startup_diagnostics", bind=True, max_retries=1)
def startup_diagnostics(self):
    """Log simple futures runtime readiness at startup."""

    async def _inner():
        from anchor.config import settings
        from anchor.database.engine import init_db
        from anchor.database.repositories.events import SystemEventRepository
        from anchor.database.repositories.market_data import MarketDataRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.utils.time_utils import get_session_name, utcnow
        import anchor.database.engine as _db_engine

        await init_db()
        now = utcnow()

        account_info: dict = {}
        try:
            broker = BrokerClient()
            acc = await broker.get_account_summary()
            account_info = {
                "balance": float(acc.get("balance", 0)),
                "equity": float(acc.get("NAV", 0)),
                "currency": acc.get("currency", "?"),
                "open_trade_count": acc.get("openTradeCount", 0),
            }
        except Exception as exc:
            account_info = {"error": str(exc)}

        candle_counts: dict[str, dict[str, int | str]] = {}
        async with _db_engine.AsyncSessionFactory() as session:
            repo = MarketDataRepository(session)
            for inst in _runtime_instruments():
                candle_counts[inst] = {}
                for tf in ("H1", "H4", "D"):
                    try:
                        rows = await repo.get_latest_n_candles(inst, tf, 300)
                        candle_counts[inst][tf] = len(rows)
                    except Exception:
                        candle_counts[inst][tf] = "ERROR"

        model_status: dict[str, dict[str, bool] | bool]
        required_models: dict[str, dict[str, bool]]
        if settings.trading_domain == "futures":
            model_status = {"required": False}
            required_models = {}
        else:
            model_dir = Path("/app/models")
            model_status = {
                inst: {"hmm": (model_dir / f"hmm_{inst}.pkl").exists()}
                for inst in _runtime_instruments()
            }
            model_status["hmm_latest"] = (model_dir / "hmm_latest.pkl").exists()
            required_models = {
                inst: status
                for inst, status in model_status.items()
                if isinstance(status, dict) and not all(status.values())
            }

        redis_ok = False
        try:
            import redis.asyncio as aioredis

            rc = aioredis.from_url(settings.redis_url, decode_responses=True)
            await rc.ping()
            redis_ok = True
            await rc.aclose()
        except Exception:
            redis_ok = False

        severity = "WARN" if required_models or not redis_ok else "INFO"
        message_parts = []
        if required_models:
            message_parts.append(f"missing_required_models={len(required_models)}")
        if not redis_ok:
            message_parts.append("redis_unavailable")
        if not message_parts:
            message_parts.append("startup diagnostics clean")

        logger.info(
            "startup_diagnostics",
            utc_time=now.isoformat(),
            session=get_session_name(now),
            account=account_info,
            candle_counts=candle_counts,
            models=model_status,
            redis_ok=redis_ok,
            broker_provider=settings.broker_provider,
            ibkr_account_id_set=bool(settings.broker_account_id),
        )

        async with _db_engine.AsyncSessionFactory() as session:
            await SystemEventRepository(session).insert(
                event_type="STARTUP_DIAGNOSTICS",
                severity=severity,
                component="ENGINE",
                message="; ".join(message_parts),
                metadata={
                    "account": account_info,
                    "candle_counts": candle_counts,
                    "models": model_status,
                    "required_models": required_models,
                    "redis_ok": redis_ok,
                },
            )
            await session.commit()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("startup_diagnostics_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)
