import asyncio
import json
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from anchor.config import get_settings
from anchor.database.engine import init_db, close_db
from anchor.utils.logging import configure_logging
from anchor.api.routers import positions, orders, performance, system
from anchor.api.websocket import router as ws_router, manager as ws_manager
from anchor.monitoring.heartbeat import HeartbeatService
from anchor.api.routers.system import set_stream_status, set_account_info, set_open_positions_count, set_cached_broker_state
from anchor.scheduler._shared import _spread_monitor as _shared_spread_monitor
from anchor.risk.weekend_guard import WeekendGuard
from anchor.execution.broker_client import BrokerClient

logger = structlog.get_logger(__name__)
settings = get_settings()


async def _reconcile_account(broker_client, stream_client, redis_client=None) -> None:
    """Poll the configured broker every 30s and broadcast account/position state."""
    from anchor.utils.time_utils import utcnow

    while stream_client._running:
        try:
            acc = await broker_client.get_account_summary()
            balance = float(acc.get("balance", 0))
            equity = float(acc.get("NAV", balance))
            set_account_info(balance=balance, equity=equity, reconciled_at=utcnow().isoformat())

            raw_trades = await broker_client.get_open_trades()
            set_open_positions_count(len(raw_trades))
            try:
                raw_orders = await broker_client.get_pending_orders()
            except Exception:
                raw_orders = []
            set_cached_broker_state(raw_trades, raw_orders)

            if redis_client:
                await redis_client.publish("account", json.dumps({
                    "channel": "account",
                    "data": {"balance": balance, "equity": equity},
                }))
                positions = []
                for t in raw_trades:
                    units = float(t.get("currentUnits", t.get("initialUnits", 0)) or 0)
                    positions.append({
                        "broker_trade_id": t.get("id"),
                        "instrument":      t.get("instrument"),
                        "direction":       "LONG" if units > 0 else "SHORT",
                        "units":           abs(units),
                        "avg_entry_price": float(t.get("price", 0)),
                        "current_price":   float(t.get("price", 0)),
                        "unrealized_pl":   float(t.get("unrealizedPL", 0)),
                        "stop_loss":       float(t["stopLossOrder"]["price"]) if "stopLossOrder" in t else None,
                        "take_profit":     float(t["takeProfitOrder"]["price"]) if "takeProfitOrder" in t else None,
                        "status":          "OPEN",
                    })
                await redis_client.publish("positions", json.dumps({
                    "channel": "positions",
                    "data": positions,
                }))

        except Exception as exc:
            logger.warning("reconcile_error", error=str(exc))
        await asyncio.sleep(7)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    logger.info("anchor_starting", env=settings.app_env, service=settings.service_name)

    await init_db()

    heartbeat: HeartbeatService | None = None
    stream_client = None
    weekend_guard: WeekendGuard | None = None

    if settings.service_name == "engine":
        heartbeat = HeartbeatService()
        asyncio.create_task(heartbeat.run())
        logger.info("heartbeat_started")

        import redis.asyncio as aioredis
        from anchor.data.ibkr_stream import IBKRStreamClient as StreamClient

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

        class _TickRepo:
            async def insert_tick(self, instrument, bid, ask, time):
                from anchor.database.engine import AsyncSessionFactory as _SF
                from anchor.database.models import TickData
                if _SF is None:
                    return
                async with _SF() as session:
                    session.add(TickData(instrument=instrument, bid=bid, ask=ask, time=time, source=settings.broker_provider))
                    await session.commit()

        stream_client = StreamClient(
            redis_client=redis_client,
            tick_repo=_TickRepo(),
            spread_monitor=_shared_spread_monitor,
        )

        async def _stream_with_status():
            try:
                while True:
                    set_stream_status(stream_client.connected)
                    await stream_client.run()
                    if not stream_client._running:
                        break
            finally:
                set_stream_status(False)

        async def _stream_status_heartbeat():
            while stream_client and stream_client._running:
                set_stream_status(stream_client.connected)
                await asyncio.sleep(1)

        async def _redis_fanout():
            """Subscribe to Redis channels and broadcast to WebSocket clients."""
            pubsub = redis_client.pubsub()
            await pubsub.subscribe("ticks", "regime", "signals", "positions", "orders", "account")
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        payload = json.loads(message["data"])
                        await ws_manager.broadcast(
                            payload.get("channel", "ticks"),
                            payload.get("data", payload),
                        )
                    except Exception as exc:
                        logger.warning("fanout_error", error=str(exc))

        asyncio.create_task(_stream_with_status())
        asyncio.create_task(_stream_status_heartbeat())
        asyncio.create_task(_reconcile_account(BrokerClient(), stream_client, redis_client))
        asyncio.create_task(_redis_fanout())
        logger.info("broker_stream_started", provider=settings.broker_provider.lower())

        _wg_broker = BrokerClient()

        class _WGPositionRepo:
            """Thin adapter: opens its own session for each WeekendGuard call."""
            async def get_open(self):
                from anchor.database.engine import AsyncSessionFactory as _SF
                from anchor.database.repositories.positions import PositionRepository as _PRepo
                if _SF is None:
                    return []
                async with _SF() as session:
                    return await _PRepo(session).get_open()

        if settings.trading_domain != "futures" or settings.futures_weekend_guard_enabled:
            weekend_guard = WeekendGuard(broker_client=_wg_broker, position_repo=_WGPositionRepo())
            asyncio.create_task(weekend_guard.run())
            logger.info("weekend_guard_started")

    yield

    if stream_client:
        await stream_client.stop()
        set_stream_status(False)

    if weekend_guard:
        await weekend_guard.stop()

    if heartbeat:
        await heartbeat.stop()

    await close_db()
    logger.info("anchor_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Anchor — Autonomous Futures Trading System",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.app_env == "development" else None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = "/api/v1"
    app.include_router(positions.router, prefix=prefix, tags=["positions"])
    app.include_router(orders.router, prefix=prefix, tags=["orders"])
    app.include_router(performance.router, prefix=prefix, tags=["performance"])
    app.include_router(system.router, prefix=prefix, tags=["system"])
    app.include_router(ws_router)

    return app


app = create_app()


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
