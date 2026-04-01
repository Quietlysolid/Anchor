"""Snapshot and diagnostics tasks for the futures runtime."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _drawdown_monitor, _run_async
from anchor.futures.io import load_daily_market_closes

logger = structlog.get_logger(__name__)

def _account_margin_usage_pct(account: dict) -> float | None:
    nav = float(account.get("NAV", 0.0) or 0.0)
    maint = float(account.get("fullMaintMarginReq", account.get("maintMarginReq", 0.0)) or 0.0)
    if nav <= 0 or maint <= 0:
        return None
    return maint / nav


def _runtime_instruments() -> list[str]:
    from anchor.config import settings

    if settings.trading_domain == "futures":
        return list(settings.futures_v1_markets)
    return list(settings.instruments)


def _vol_based_emergency_stop(
    data_dir: str,
    instrument: str,
    direction: str,
    reference_price: float,
    vol_lookback_days: int,
) -> float | None:
    market_id = str(instrument).upper().split("-", 1)[0]
    if reference_price <= 0:
        return None
    try:
        closes = load_daily_market_closes(data_dir=data_dir, markets=[market_id])[market_id].dropna()
    except Exception:
        return None
    returns = closes.pct_change(fill_method=None).dropna()
    history = returns.tail(max(vol_lookback_days, 5))
    if history.empty:
        return None
    daily_vol = float(history.std(ddof=0) or 0.0)
    if daily_vol <= 0:
        return None
    stop_distance = reference_price * daily_vol * 2.5
    if direction == "LONG":
        return max(0.0, reference_price - stop_distance)
    return reference_price + stop_distance


@celery_app.task(name="anchor.scheduler.jobs.snapshot_equity", bind=True, max_retries=3)
def snapshot_equity(self):
    """Write an equity curve point every 15 minutes."""

    async def _inner():
        from anchor.database.engine import get_session, init_db
        from anchor.database.models import EquityCurvePoint
        from anchor.database.repositories import EquityRepository
        from anchor.database.repositories.positions import PositionRepository
        from anchor.database.repositories.events import SystemEventRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.config import settings

        await init_db()
        client = BrokerClient()
        account = await client.get_account_summary()
        if not account:
            logger.warning("snapshot_equity_no_account")
            return

        balance = float(account.get("balance", 0))
        equity = float(account.get("NAV", balance))
        unrealized = equity - balance
        margin_usage_pct = _account_margin_usage_pct(account)

        async with get_session() as session:
            repo = EquityRepository(session)
            pos_repo = PositionRepository(session)
            latest = await repo.get_latest()
            peak = max(equity, float(latest.peak_equity) if latest else equity)

            await _drawdown_monitor.bootstrap_peak_equity(session)
            await _drawdown_monitor.bootstrap_month_state(session, current_equity=equity)
            _drawdown_monitor.update(equity)
            dd = _drawdown_monitor.current_drawdown
            drawdown_allowed, drawdown_reason = _drawdown_monitor.check()

            point = EquityCurvePoint(
                time=datetime.now(timezone.utc),
                account_balance=balance,
                account_equity=equity,
                unrealized_pl=unrealized,
                drawdown_pct=dd * 100,
                peak_equity=max(peak, equity),
            )
            await repo.insert(point)
            if settings.trading_domain == "futures":
                open_positions = await client.get_open_positions()
                tracked_positions = {
                    position.broker_trade_id: position
                    for position in await pos_repo.get_open()
                    if position.broker_trade_id
                }
                for position in open_positions:
                    trade_id = str(position.get("id", ""))
                    instrument = str(position.get("instrument", ""))
                    tracked = tracked_positions.get(trade_id)
                    if tracked is None or tracked.broker_stop_order_id:
                        continue
                    if not instrument or "-" not in instrument:
                        continue
                    current_units = float(position.get("currentUnits", 0.0) or 0.0)
                    if abs(current_units) < 1e-9:
                        continue
                    direction = "LONG" if current_units > 0 else "SHORT"
                    reference_price = float(position.get("marketPrice", 0.0) or position.get("price", 0.0) or 0.0)
                    stop_price = _vol_based_emergency_stop(
                        data_dir="/app/data",
                        instrument=instrument,
                        direction=direction,
                        reference_price=reference_price,
                        vol_lookback_days=settings.futures_vol_lookback_days,
                    )
                    if stop_price is None:
                        continue
                    stop_result = await client.ensure_protective_stop(instrument, stop_price)
                    if stop_result.get("status") == "placed":
                        await pos_repo.set_broker_stop_order(
                            broker_trade_id=trade_id,
                            stop_order_id=str(stop_result.get("stop_order_id")),
                            stop_loss=stop_price,
                        )
                        logger.info(
                            "futures_catastrophe_stop_backfilled",
                            instrument=instrument,
                            stop_order_id=stop_result.get("stop_order_id"),
                            stop_price=stop_price,
                        )
            if (
                settings.trading_domain == "futures"
                and margin_usage_pct is not None
                and margin_usage_pct >= settings.futures_margin_warn_usage_pct
            ):
                await SystemEventRepository(session).insert(
                    event_type="FUTURES_MARGIN_GUARD",
                    severity="WARN",
                    component="RISK",
                    message=(
                        f"Futures margin usage warning: {margin_usage_pct:.1%} "
                        f"(threshold {settings.futures_margin_warn_usage_pct:.1%})"
                    ),
                    metadata={
                        "live_margin_usage_pct": margin_usage_pct,
                        "warn_threshold": settings.futures_margin_warn_usage_pct,
                        "block_new_opens_threshold": settings.futures_margin_block_new_opens_pct,
                        "available_funds": float(account.get("availableFunds", 0.0) or 0.0),
                        "excess_liquidity": float(account.get("excessLiquidity", 0.0) or 0.0),
                        "maint_margin_req": float(account.get("maintMarginReq", 0.0) or 0.0),
                        "full_maint_margin_req": float(account.get("fullMaintMarginReq", 0.0) or 0.0),
                    },
                )
            if settings.trading_domain == "futures" and not drawdown_allowed:
                await SystemEventRepository(session).insert(
                    event_type="FUTURES_DRAWDOWN_GUARD",
                    severity="WARN",
                    component="RISK",
                    message=f"Futures drawdown guard active: {drawdown_reason}",
                    metadata={
                        "drawdown_pct": dd,
                        "drawdown_reason": drawdown_reason,
                        "scale_factor": _drawdown_monitor.scale_factor,
                    },
                )
            await session.commit()

        from anchor.monitoring import metrics

        metrics.account_balance.set(balance)
        metrics.account_equity.set(equity)
        metrics.unrealized_pl.set(unrealized)
        metrics.drawdown_pct.set(dd * 100)
        logger.debug("equity_snapshot_written", balance=balance, equity=equity, margin_usage_pct=margin_usage_pct)

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
