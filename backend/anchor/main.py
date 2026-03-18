import asyncio
import json
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from anchor.config import get_settings
from anchor.database.engine import init_db, close_db, AsyncSessionFactory
from anchor.utils.logging import configure_logging
from anchor.api.routers import (
    market_data,
    signals,
    positions,
    orders,
    performance,
    regime,
    system,
    calendar,
    backtest,
    market,
    intelligence,
)
from anchor.api.websocket import router as ws_router, manager as ws_manager
from anchor.monitoring.heartbeat import HeartbeatService
from anchor.data.oanda_stream import OANDAStreamClient
from anchor.api.routers.system import set_stream_status, set_account_info
from anchor.scheduler._shared import _spread_monitor as _shared_spread_monitor
from anchor.risk.weekend_guard import WeekendGuard

logger = structlog.get_logger(__name__)
settings = get_settings()


async def _reconcile_account(stream_client: OANDAStreamClient, redis_client=None) -> None:
    """Poll OANDA REST API every 60s — update balance/equity and broadcast positions via WS."""
    from oandapyV20 import API
    from oandapyV20.endpoints.accounts import AccountDetails
    from anchor.utils.time_utils import utcnow

    client = API(access_token=settings.oanda_api_key, environment=settings.oanda_environment)
    while stream_client._running:
        try:
            r = AccountDetails(settings.oanda_account_id)
            client.request(r)
            acc     = r.response["account"]
            balance = float(acc["balance"])
            equity  = float(acc["NAV"])
            set_account_info(balance=balance, equity=equity, reconciled_at=utcnow().isoformat())

            if redis_client:
                # Push balance/equity to dashboard without REST polling
                await redis_client.publish("account", json.dumps({
                    "channel": "account",
                    "data": {"balance": balance, "equity": equity},
                }))
                # Push open positions with live unrealized P&L straight from OANDA
                raw_trades = acc.get("trades", [])
                positions = []
                for t in raw_trades:
                    units = float(t.get("currentUnits", 0))
                    positions.append({
                        "oanda_trade_id":  t.get("id"),
                        "instrument":      t.get("instrument"),
                        "direction":       "LONG" if units > 0 else "SHORT",
                        "units":           abs(units),
                        "avg_entry_price": float(t.get("price", 0)),
                        "current_price":   float(t.get("price", 0)),
                        "unrealized_pl":   float(t.get("unrealizedPL", 0)),
                        "stop_loss":       float(t["stopLossOrder"]["price"])   if "stopLossOrder"   in t else None,
                        "take_profit":     float(t["takeProfitOrder"]["price"]) if "takeProfitOrder" in t else None,
                        "status":          "OPEN",
                    })
                await redis_client.publish("positions", json.dumps({
                    "channel": "positions",
                    "data": positions,
                }))

        except Exception as exc:
            logger.warning("reconcile_error", error=str(exc))
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    logger.info("anchor_starting", env=settings.app_env, service=settings.service_name)

    await init_db()

    heartbeat: HeartbeatService | None = None
    stream_client: OANDAStreamClient | None = None
    weekend_guard: WeekendGuard | None = None

    if settings.service_name == "engine":
        heartbeat = HeartbeatService()
        asyncio.create_task(heartbeat.run())
        logger.info("heartbeat_started")

        # Start OANDA price stream
        if settings.oanda_api_key and settings.oanda_api_key != "your_oanda_api_key_here":
            import redis.asyncio as aioredis
            from anchor.database.repositories.market_data import MarketDataRepository

            redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

            # Thin wrapper so the stream can persist ticks via a fresh session per write
            class _TickRepo:
                async def insert_tick(self, instrument, bid, ask, time):
                    from anchor.database.engine import AsyncSessionFactory as _SF
                    from anchor.database.models import TickData
                    if _SF is None:
                        return
                    async with _SF() as session:
                        session.add(TickData(instrument=instrument, bid=bid, ask=ask, time=time, source="oanda"))
                        await session.commit()

            stream_client = OANDAStreamClient(
                redis_client=redis_client,
                tick_repo=_TickRepo(),
                spread_monitor=_shared_spread_monitor,
            )

            async def _stream_with_status():
                set_stream_status(True)
                try:
                    await stream_client.run()
                finally:
                    set_stream_status(False)

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
            asyncio.create_task(_reconcile_account(stream_client, redis_client))
            asyncio.create_task(_redis_fanout())
            logger.info("oanda_stream_started")

            # Start weekend gap guard — closes all positions by Friday 20:30 UTC
            from anchor.execution.broker_client import BrokerClient as _BC
            from anchor.database.repositories.positions import PositionRepository as _PR
            _wg_broker = _BC()

            class _WGPositionRepo:
                """Thin adapter: opens its own session for each WeekendGuard call."""
                async def get_open(self):
                    from anchor.database.engine import AsyncSessionFactory as _SF
                    from anchor.database.repositories.positions import PositionRepository as _PRepo
                    if _SF is None:
                        return []
                    async with _SF() as session:
                        return await _PRepo(session).get_open()

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
        title="Anchor — Autonomous Forex Trading System",
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
    app.include_router(market_data.router, prefix=prefix, tags=["market-data"])
    app.include_router(signals.router, prefix=prefix, tags=["signals"])
    app.include_router(positions.router, prefix=prefix, tags=["positions"])
    app.include_router(orders.router, prefix=prefix, tags=["orders"])
    app.include_router(performance.router, prefix=prefix, tags=["performance"])
    app.include_router(regime.router, prefix=prefix, tags=["regime"])
    app.include_router(system.router, prefix=prefix, tags=["system"])
    app.include_router(calendar.router,  prefix=prefix, tags=["calendar"])
    app.include_router(backtest.router,  prefix=prefix, tags=["backtest"])
    app.include_router(market.router,       prefix=prefix, tags=["market"])
    app.include_router(intelligence.router, prefix=prefix, tags=["intelligence"])
    app.include_router(ws_router)

    return app


app = create_app()


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
