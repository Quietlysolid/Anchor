"""
OANDA REST API broker client (thin abstraction layer).
Wraps oandapyV20 with async support via executor.
"""
from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import oandapyV20
import oandapyV20.endpoints.orders as orders_ep
import oandapyV20.endpoints.trades as trades_ep
import oandapyV20.endpoints.positions as positions_ep
import oandapyV20.endpoints.accounts as accounts_ep
import structlog

from anchor.config import get_settings

if TYPE_CHECKING:
    from anchor.execution.order_types import OrderRequest

logger = structlog.get_logger(__name__)
settings = get_settings()


class BrokerClient:
    def __init__(self):
        self._client = oandapyV20.API(
            access_token=settings.oanda_api_key,
            environment=settings.oanda_environment,
        )
        self._account_id = settings.oanda_account_id
        self._executor = ThreadPoolExecutor(max_workers=4)

    async def _run(self, endpoint):
        """Run a synchronous oandapyV20 request in a thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: self._client.request(endpoint),
        )

    async def place_order(
        self,
        order_id: uuid.UUID,
        request: "OrderRequest",
    ) -> str:
        """Submit order to OANDA. Returns OANDA order ID."""
        units_str = str(request.units) if request.direction.value == "LONG" else str(-request.units)

        if request.order_type.value == "LIMIT" and request.gtd_time is not None:
            time_in_force = "GTD"
        elif request.order_type.value == "MARKET":
            time_in_force = "FOK"
        else:
            time_in_force = "GTC"

        order_body: dict = {
            "order": {
                "type":        request.order_type.value,
                "instrument":  request.instrument,
                "units":       units_str,
                "timeInForce": time_in_force,
                "positionFill": "DEFAULT",
            }
        }

        if time_in_force == "GTD" and request.gtd_time is not None:
            order_body["order"]["gtdTime"] = request.gtd_time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")

        if request.stop_loss is not None:
            order_body["order"]["stopLossOnFill"] = {
                "price": str(round(request.stop_loss, 5))
            }

        if request.take_profit is not None:
            order_body["order"]["takeProfitOnFill"] = {
                "price": str(round(request.take_profit, 5))
            }

        if request.limit_price is not None and request.order_type.value == "LIMIT":
            order_body["order"]["price"] = str(round(request.limit_price, 5))

        if request.trailing_stop_distance is not None:
            order_body["order"]["trailingStopLossOnFill"] = {
                "distance": str(round(request.trailing_stop_distance, 5))
            }

        ep = orders_ep.OrderCreate(self._account_id, data=order_body)
        resp = await self._run(ep)

        fill_tx  = resp.get("orderFillTransaction", {})
        create_tx = resp.get("orderCreateTransaction", {})

        oanda_order_id = fill_tx.get("id") or create_tx.get("id") or "unknown"
        fill_price = float(fill_tx["price"]) if fill_tx.get("price") else None
        trade_id   = fill_tx.get("tradeOpened", {}).get("tradeID")

        logger.info(
            "order_placed",
            oanda_order_id=oanda_order_id,
            trade_id=trade_id,
            fill_price=fill_price,
            instrument=request.instrument,
        )
        return oanda_order_id, fill_price, trade_id

    async def cancel_order(self, oanda_order_id: str) -> None:
        ep = orders_ep.OrderCancel(self._account_id, oanda_order_id)
        try:
            await self._run(ep)
        except Exception as exc:
            logger.warning("cancel_failed", oanda_order_id=oanda_order_id, error=str(exc))

    async def close_trade(self, trade_id: str, units: str = "ALL") -> dict:
        data = {} if units == "ALL" else {"units": units}
        ep = trades_ep.TradeClose(self._account_id, trade_id, data=data)
        return await self._run(ep)

    async def get_open_trades(self) -> list[dict]:
        ep = trades_ep.TradesList(self._account_id, params={"state": "OPEN"})
        resp = await self._run(ep)
        return resp.get("trades", [])

    async def get_open_positions(self) -> list[dict]:
        ep = positions_ep.PositionList(self._account_id)
        resp = await self._run(ep)
        return resp.get("positions", [])

    async def get_account_summary(self) -> dict:
        ep = accounts_ep.AccountSummary(self._account_id)
        resp = await self._run(ep)
        return resp.get("account", {})

    async def get_pending_orders(self) -> list[dict]:
        ep = orders_ep.OrderList(self._account_id, params={"state": "PENDING"})
        resp = await self._run(ep)
        return resp.get("orders", [])

    async def get_closed_trade(self, trade_id: str) -> dict | None:
        """Fetch a single closed trade by OANDA trade ID."""
        try:
            ep = trades_ep.TradeDetails(self._account_id, trade_id)
            resp = await self._run(ep)
            return resp.get("trade")
        except Exception as exc:
            logger.warning("get_closed_trade_failed", trade_id=trade_id, error=str(exc))
            return None
