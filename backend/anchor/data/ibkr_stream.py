"""
IBKR streaming market data for a configured instrument list.

This is intentionally minimal: it subscribes to top-of-book quotes and
publishes bid/ask snapshots to Redis + TickData, matching the shape the rest
of Anchor already expects from the OANDA stream path.
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone

import structlog

from anchor.config import get_settings
from anchor.execution.ibkr_client import IBKRDependencyMissingError
from anchor.execution.ibkr_utils import parse_ibkr_instrument

logger = structlog.get_logger(__name__)
settings = get_settings()

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.wrapper import EWrapper
except ImportError:  # pragma: no cover - dependency missing path
    EClient = object  # type: ignore[assignment]
    EWrapper = object  # type: ignore[assignment]
    Contract = None  # type: ignore[assignment]


class _IBKRStreamApp(EWrapper, EClient):  # type: ignore[misc]
    def __init__(self, on_quote) -> None:
        EClient.__init__(self, self)
        self.on_quote = on_quote
        self.connected_event = threading.Event()
        self.quote_state: dict[int, dict] = {}
        self._delayed_requested = False

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.connected_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N802,E501
        if errorCode == 354 and not self._delayed_requested:
            self._delayed_requested = True
            try:
                self.reqMarketDataType(3)
                logger.info("ibkr_stream_switched_to_delayed_data")
            except Exception:
                logger.warning("ibkr_stream_delayed_fallback_failed", exc_info=True)
        if errorCode not in {2104, 2106, 2158, 2108}:
            logger.warning("ibkr_stream_error", req_id=reqId, code=errorCode, error=errorString)

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib) -> None:  # noqa: N802
        quote = self.quote_state.setdefault(reqId, {})
        if tickType == 1:
            quote["bid"] = price
        elif tickType == 2:
            quote["ask"] = price
        elif tickType == 4:
            quote["last"] = price
        if "bid" in quote and "ask" in quote:
            self.on_quote(reqId, quote)


class IBKRStreamClient:
    def __init__(self, redis_client=None, tick_repo=None, spread_monitor=None):
        if Contract is None:
            raise IBKRDependencyMissingError(
                "IBKR stream support requires the 'ibapi' package to be installed."
            )

        self.redis = redis_client
        self.tick_repo = tick_repo
        self.spread_monitor = spread_monitor
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._app = _IBKRStreamApp(self._handle_quote_from_thread)
        self._ticker_to_instrument: dict[int, str] = {}
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def run(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()
        while self._running:
            try:
                await asyncio.to_thread(self._connect_and_stream)
            except Exception as exc:
                self._connected = False
                logger.warning("ibkr_stream_connect_failed", error=str(exc))
                await asyncio.sleep(5)
            finally:
                self._connected = False

    def _stream_instruments(self) -> list[str]:
        if settings.trading_domain == "futures":
            return list(settings.futures_v1_markets)
        return list(settings.instruments)

    def _connect_and_stream(self) -> None:
        self._app = _IBKRStreamApp(self._handle_quote_from_thread)
        self._ticker_to_instrument = {}
        self._app.connect(settings.ibkr_host, settings.ibkr_port, clientId=settings.ibkr_client_id + 9000)
        self._thread = threading.Thread(target=self._app.run, daemon=True, name="ibkr-stream")
        self._thread.start()
        if not self._app.connected_event.wait(timeout=10):
            self._safe_disconnect()
            raise RuntimeError("IBKR stream connection timed out")

        self._connected = True
        self._app.reqMarketDataType(settings.ibkr_market_data_type)
        for ticker_id, instrument in enumerate(self._stream_instruments(), start=1):
            parsed = parse_ibkr_instrument(instrument)
            contract = Contract()
            contract.symbol = parsed.symbol
            contract.secType = parsed.sec_type
            contract.exchange = parsed.exchange
            contract.currency = parsed.currency
            if parsed.sec_type == "CASH":
                contract.localSymbol = f"{parsed.symbol}.{parsed.currency}"
            elif parsed.sec_type == "FUT":
                if parsed.last_trade_date_or_contract_month:
                    contract.lastTradeDateOrContractMonth = parsed.last_trade_date_or_contract_month
                if parsed.multiplier:
                    contract.multiplier = parsed.multiplier
                if parsed.trading_class:
                    contract.tradingClass = parsed.trading_class
            self._ticker_to_instrument[ticker_id] = parsed.raw
            self._app.reqMktData(ticker_id, contract, "", False, False, [])

        while self._running:
            time_to_sleep = 1.0
            threading.Event().wait(time_to_sleep)
        self._safe_disconnect()

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        if self._app.isConnected():
            await asyncio.to_thread(self._app.disconnect)

    def _safe_disconnect(self) -> None:
        try:
            if self._app.isConnected():
                self._app.disconnect()
        except Exception:
            logger.debug("ibkr_stream_disconnect_ignored", exc_info=True)

    def _handle_quote_from_thread(self, req_id: int, quote: dict) -> None:
        if self._loop is None:
            return
        instrument = self._ticker_to_instrument.get(req_id)
        if instrument is None:
            return
        asyncio.run_coroutine_threadsafe(self._publish_quote(instrument, quote), self._loop)

    async def _publish_quote(self, instrument: str, quote: dict) -> None:
        bid = float(quote["bid"])
        ask = float(quote["ask"])
        now = datetime.now(timezone.utc)
        tick = {
            "instrument": instrument,
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2,
            "spread": ask - bid,
            "time": now.isoformat(),
        }

        if self.redis:
            await self.redis.publish("ticks", json.dumps({"channel": "ticks", "data": tick}))
            await self.redis.set(
                f"spread:{instrument}",
                json.dumps({"bid": bid, "ask": ask, "spread": ask - bid}),
                ex=30,
            )

        if self.spread_monitor:
            self.spread_monitor.update(instrument, bid, ask)

        if self.tick_repo:
            await self.tick_repo.insert_tick(instrument=instrument, bid=bid, ask=ask, time=now)
