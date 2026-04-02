"""
IBKR TWS / Gateway broker client.

This keeps the existing BrokerClient surface Anchor already expects while
normalizing IBKR's order/position model into the current OANDA-shaped app.
For phase 1, positions are tracked net by instrument. The returned "trade id"
is therefore the normalized instrument symbol, which allows the existing
close/partial-close paths to flatten by instrument without a DB migration.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from anchor.config import get_settings
from anchor.execution.ibkr_utils import (
    ibkr_position_id,
    normalize_instrument_symbol,
    parse_ibkr_instrument,
)

if TYPE_CHECKING:
    from anchor.execution.order_types import OrderRequest

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.order import Order
    from ibapi.wrapper import EWrapper
except ImportError:  # pragma: no cover - exercised only when dependency is missing
    EClient = object  # type: ignore[assignment]
    EWrapper = object  # type: ignore[assignment]
    Contract = None  # type: ignore[assignment]
    Order = None  # type: ignore[assignment]

logger = structlog.get_logger(__name__)
settings = get_settings()
_CLIENT_ID_COUNTER = itertools.count()


def _normalize_position_entry_price(contract, raw_cost: float) -> float:
    if not raw_cost:
        return 0.0
    if getattr(contract, 'secType', None) != 'FUT':
        return float(raw_cost)

    multiplier_raw = getattr(contract, 'multiplier', None)
    try:
        multiplier = float(multiplier_raw) if multiplier_raw else 0.0
    except (TypeError, ValueError):
        multiplier = 0.0

    if multiplier > 0:
        return float(raw_cost) / multiplier
    return float(raw_cost)


def _broker_client_id_base(service_name: str) -> int:
    """Reserve stable client-id ranges per process role to avoid IBKR collisions."""
    ranges = {
        "engine": 10000,
        "celery_worker": 20000,
        "celery_beat": 30000,
        "watchdog": 40000,
    }
    return ranges.get(service_name, 50000)


class IBKRDependencyMissingError(RuntimeError):
    pass


@dataclass
class _RequestState:
    done: threading.Event = field(default_factory=threading.Event)
    rows: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class _OrderState:
    done: threading.Event = field(default_factory=threading.Event)
    status: str | None = None
    avg_fill_price: float | None = None
    filled: float = 0.0
    remaining: float = 0.0
    error: str | None = None


class _IBKRApp(EWrapper, EClient):  # type: ignore[misc]
    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.connected_event = threading.Event()
        self.next_valid_id_event = threading.Event()
        self.next_order_id: int | None = None
        self.errors: "queue.Queue[str]" = queue.Queue()
        self.account_summary_req: _RequestState | None = None
        self.account_updates_req: _RequestState | None = None
        self.positions_req: _RequestState | None = None
        self.open_orders_req: _RequestState | None = None
        self.contract_details_req: _RequestState | None = None
        self.order_states: dict[int, _OrderState] = {}
        self.market_data: dict[int, dict[str, Any]] = {}
        self.tick_callback = None
        self.portfolio_rows: list[dict] = []

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.next_order_id = orderId
        self.next_valid_id_event.set()
        self.connected_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N802,E501
        message = f"{reqId}:{errorCode}:{errorString}"
        if advancedOrderRejectJson:
            message = f"{message}:{advancedOrderRejectJson}"
        self.errors.put(message)
        ignored_codes = {2104, 2106, 2107, 2108, 2119, 2158}
        if reqId in self.order_states:
            self.order_states[reqId].error = message
            self.order_states[reqId].done.set()
        if self.account_summary_req and errorCode not in ignored_codes:
            self.account_summary_req.error = message
        if self.account_updates_req and errorCode not in ignored_codes:
            self.account_updates_req.error = message
        if self.positions_req and errorCode not in ignored_codes:
            self.positions_req.error = message
        if self.open_orders_req and errorCode not in ignored_codes:
            self.open_orders_req.error = message
            self.open_orders_req.done.set()
        if self.contract_details_req and errorCode not in ignored_codes:
            self.contract_details_req.error = message
            self.contract_details_req.done.set()

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802,E501
        if self.account_summary_req is not None:
            self.account_summary_req.rows.append(
                {"account": account, "tag": tag, "value": value, "currency": currency}
            )

    def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
        if self.account_summary_req is not None:
            self.account_summary_req.done.set()

    def updateAccountValue(self, key: str, val: str, currency: str, accountName: str) -> None:  # noqa: N802
        if self.account_updates_req is not None:
            self.account_updates_req.rows.append(
                {"account": accountName, "tag": key, "value": val, "currency": currency}
            )

    def accountDownloadEnd(self, accountName: str) -> None:  # noqa: N802
        if self.account_updates_req is not None:
            self.account_updates_req.done.set()

    def updatePortfolio(self, contract, position: float, marketPrice: float, marketValue: float, averageCost: float, unrealizedPNL: float, realizedPNL: float, accountName: str) -> None:  # noqa: N802,E501
        self.portfolio_rows.append({
            "contract": contract,
            "position": position,
            "marketPrice": marketPrice,
            "averageCost": averageCost,
            "unrealizedPNL": unrealizedPNL,
            "realizedPNL": realizedPNL,
        })

    def position(self, account: str, contract, position: float, avgCost: float) -> None:  # noqa: N802
        if self.positions_req is not None:
            self.positions_req.rows.append(
                {
                    "account": account,
                    "contract": contract,
                    "position": position,
                    "avgCost": avgCost,
                }
            )

    def positionEnd(self) -> None:  # noqa: N802
        if self.positions_req is not None:
            self.positions_req.done.set()

    def openOrder(self, orderId: int, contract, order, orderState) -> None:  # noqa: N802
        if self.open_orders_req is not None:
            self.open_orders_req.rows.append(
                {
                    "orderId": orderId,
                    "contract": contract,
                    "order": order,
                    "state": getattr(orderState, "status", None),
                }
            )

    def openOrderEnd(self) -> None:  # noqa: N802
        if self.open_orders_req is not None:
            self.open_orders_req.done.set()

    def contractDetails(self, reqId: int, contractDetails) -> None:  # noqa: N802
        if self.contract_details_req is not None:
            self.contract_details_req.rows.append({"contract": contractDetails.contract})

    def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        if self.contract_details_req is not None:
            self.contract_details_req.done.set()

    def orderStatus(  # noqa: N802
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        state = self.order_states.get(orderId)
        if state is None:
            return
        state.status = status
        state.filled = filled
        state.remaining = remaining
        state.avg_fill_price = avgFillPrice or lastFillPrice or state.avg_fill_price
        if status in {"Filled", "Cancelled", "ApiCancelled", "Inactive"}:
            state.done.set()

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib) -> None:  # noqa: N802
        data = self.market_data.setdefault(reqId, {})
        if tickType == 1:
            data["bid"] = price
        elif tickType == 2:
            data["ask"] = price
        elif tickType == 4:
            data["last"] = price
        if self.tick_callback:
            self.tick_callback(reqId, data)


class IBKRBrokerClient:
    def __init__(self) -> None:
        if Contract is None or Order is None:
            raise IBKRDependencyMissingError(
                "IBKR support requires the 'ibapi' package to be installed in the backend environment."
            )

        self._host = settings.ibkr_host
        self._port = settings.ibkr_port
        process_offset = (os.getpid() % 1000) * 10
        self._client_id = _broker_client_id_base(settings.service_name) + process_offset + next(_CLIENT_ID_COUNTER)
        self._account_id = settings.broker_account_id
        self._app = _IBKRApp()
        self._thread: threading.Thread | None = None
        self._connect_lock = threading.Lock()
        # Serialize all blocking IBKR API calls so shared _app state
        # (_app.positions_req, _app.account_updates_req, etc.) is never
        # accessed by two threads simultaneously.
        self._api_lock = threading.Lock()

    @staticmethod
    def _wait_for_request(state: _RequestState, timeout: float, min_rows: int = 1) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if state.done.wait(timeout=0.2):
                return True
            if len(state.rows) >= min_rows:
                return True
        return state.done.is_set() or len(state.rows) >= min_rows

    def _ensure_connected(self) -> None:
        with self._connect_lock:
            if self._app.isConnected():
                return

            self._app.connect(self._host, self._port, clientId=self._client_id)
            self._thread = threading.Thread(target=self._app.run, daemon=True, name="ibkr-api")
            self._thread.start()

            if not self._app.connected_event.wait(timeout=10):
                self._app.disconnect()
                raise RuntimeError(
                    f"IBKR connection timed out for {self._host}:{self._port} client_id={self._client_id}"
                )

            if not self._app.next_valid_id_event.wait(timeout=10):
                self._app.disconnect()
                raise RuntimeError("IBKR next valid order id was not received")

            logger.info(
                "ibkr_connected",
                host=self._host,
                port=self._port,
                client_id=self._client_id,
                account_id=self._account_id,
            )

    async def _run_blocking(self, fn, *args, **kwargs):
        def _locked():
            with self._api_lock:
                return fn(*args, **kwargs)
        return await asyncio.to_thread(_locked)

    def _build_contract(self, instrument: str):
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
        return contract

    def _next_order_id(self) -> int:
        self._ensure_connected()
        if self._app.next_order_id is None:
            raise RuntimeError("IBKR next order id is unavailable")
        order_id = self._app.next_order_id
        self._app.next_order_id += 1
        return order_id

    @staticmethod
    def _normalize_account_summary_rows(rows: list[dict], account_id: str | None) -> dict:
        filtered = [row for row in rows if not account_id or row.get("account") == account_id]

        def _best_numeric(tag: str, fallback_tags: tuple[str, ...] = ()) -> float:
            for candidate_tag in (tag, *fallback_tags):
                candidates = [row for row in filtered if row.get("tag") == candidate_tag]
                if not candidates:
                    continue
                candidates.sort(
                    key=lambda row: (
                        0 if row.get("currency") == "BASE" else 1 if row.get("currency") == "USD" else 2
                    )
                )
                value = candidates[0].get("value")
                try:
                    return float(value or 0.0)
                except (TypeError, ValueError):
                    return 0.0
            return 0.0

        currency = "USD"
        currency_rows = [row for row in filtered if row.get("tag") == "Currency"]
        for row in currency_rows:
            value = str(row.get("value") or "").upper()
            if value and value != "BASE":
                currency = value
                break

        realized = _best_numeric("RealizedPnL")
        unrealized = _best_numeric("UnrealizedPnL")
        broker_day_pl = _best_numeric("FuturesPNL")
        if broker_day_pl == 0.0 and (realized or unrealized):
            broker_day_pl = realized + unrealized

        return {
            "balance": _best_numeric("TotalCashValue", fallback_tags=("CashBalance", "TotalCashBalance")),
            "NAV": _best_numeric("NetLiquidation", fallback_tags=("NetLiquidationByCurrency",)),
            "currency": currency,
            "openTradeCount": 0,
            "availableFunds": _best_numeric("AvailableFunds"),
            "excessLiquidity": _best_numeric("ExcessLiquidity"),
            "initMarginReq": _best_numeric("InitMarginReq", fallback_tags=("FullInitMarginReq",)),
            "maintMarginReq": _best_numeric("MaintMarginReq"),
            "fullMaintMarginReq": _best_numeric("FullMaintMarginReq"),
            "brokerDayPL": broker_day_pl,
        }

    def _account_summary_sync(self) -> dict:
        self._ensure_connected()
        rows: list[dict] = []

        if self._account_id:
            updates_state = _RequestState()
            self._app.account_updates_req = updates_state
            self._app.reqAccountUpdates(True, self._account_id)
            self._wait_for_request(updates_state, timeout=8)
            self._app.reqAccountUpdates(False, self._account_id)
            rows.extend(updates_state.rows)
            self._app.account_updates_req = None

        summary_state = _RequestState()
        self._app.account_summary_req = summary_state
        req_id = int(time.time() * 1000) % 2_000_000_000
        self._app.reqAccountSummary(
            req_id,
            "All",
            "AccountType,NetLiquidation,NetLiquidationByCurrency,TotalCashValue,CashBalance,TotalCashBalance,"
            "Currency,AvailableFunds,ExcessLiquidity,InitMarginReq,FullInitMarginReq,MaintMarginReq,"
            "FullMaintMarginReq,RealizedPnL,UnrealizedPnL,FuturesPNL",
        )
        self._wait_for_request(summary_state, timeout=8)
        self._app.cancelAccountSummary(req_id)
        rows.extend(summary_state.rows)
        self._app.account_summary_req = None

        if not rows:
            raise RuntimeError("IBKR account summary request timed out")
        return self._normalize_account_summary_rows(rows, self._account_id)

    async def get_account_summary(self) -> dict:
        return await self._run_blocking(self._account_summary_sync)

    def _positions_sync(self) -> list[dict]:
        self._ensure_connected()
        if not self._account_id:
            # Fall back to reqPositions if no account id configured
            state = _RequestState()
            self._app.positions_req = state
            self._app.reqPositions()
            if not state.done.wait(timeout=10):
                raise RuntimeError("IBKR positions request timed out")
            self._app.cancelPositions()
            rows = state.rows
            portfolio_map: dict[str, dict] = {}
        else:
            # Use reqAccountUpdates which fires updatePortfolio with live
            # price and unrealized P&L in addition to size/avgCost.
            self._app.portfolio_rows = []
            state = _RequestState()
            self._app.account_updates_req = state
            self._app.reqAccountUpdates(True, self._account_id)
            self._wait_for_request(state, timeout=8)
            self._app.reqAccountUpdates(False, self._account_id)
            self._app.account_updates_req = None

            # Build a lookup from symbol -> portfolio row for P&L/price
            portfolio_map = {}
            for pr in self._app.portfolio_rows:
                c = pr["contract"]
                if abs(float(pr["position"] or 0)) < 1e-9:
                    continue
                if c.secType == "FUT":
                    expiry = getattr(c, "lastTradeDateOrContractMonth", "") or ""
                    key = normalize_instrument_symbol(f"{c.symbol}-{expiry}" if expiry else c.symbol)
                else:
                    key = normalize_instrument_symbol(f"{c.symbol}_{c.currency}" if c.secType == "CASH" else c.symbol)
                portfolio_map[key] = pr

            # Convert portfolio rows to the standard position shape
            positions = []
            for instrument, pr in portfolio_map.items():
                size = float(pr["position"])
                positions.append({
                    "id": ibkr_position_id(instrument),
                    "instrument": instrument,
                    "currentUnits": size,
                    "initialUnits": size,
                    "price": _normalize_position_entry_price(pr["contract"], float(pr["averageCost"] or 0.0)),
                    "marketPrice": float(pr["marketPrice"] or 0.0),
                    "openTime": None,
                    "unrealizedPL": float(pr["unrealizedPNL"] or 0.0),
                    "realizedPL": float(pr["realizedPNL"] or 0.0),
                })
            return positions

        positions = []
        for row in rows:
            contract = row["contract"]
            size = float(row["position"] or 0.0)
            if abs(size) < 1e-9:
                continue
            if contract.secType == "CASH":
                instrument = normalize_instrument_symbol(f"{contract.symbol}_{contract.currency}")
            elif contract.secType == "FUT":
                expiry = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
                instrument = normalize_instrument_symbol(f"{contract.symbol}-{expiry}" if expiry else contract.symbol)
            else:
                instrument = normalize_instrument_symbol(contract.symbol)
            pr = portfolio_map.get(instrument, {})
            positions.append({
                "id": ibkr_position_id(instrument),
                "instrument": instrument,
                "currentUnits": size,
                "initialUnits": size,
                "price": _normalize_position_entry_price(contract, float(row["avgCost"] or 0.0)),
                "marketPrice": float(pr.get("marketPrice") or 0.0),
                "openTime": None,
                "unrealizedPL": float(pr.get("unrealizedPNL") or 0.0),
                "realizedPL": float(pr.get("realizedPNL") or 0.0),
            })
        return positions

    async def get_open_trades(self) -> list[dict]:
        return await self._run_blocking(self._positions_sync)

    async def get_open_positions(self) -> list[dict]:
        return await self.get_open_trades()

    def _pending_orders_sync(self) -> list[dict]:
        self._ensure_connected()
        state = _RequestState()
        self._app.open_orders_req = state
        self._app.reqAllOpenOrders()
        if not state.done.wait(timeout=10):
            # No pending orders is a valid state; don't raise.
            return []

        pending = []
        for row in state.rows:
            contract = row["contract"]
            if contract.secType == "CASH":
                instrument = normalize_instrument_symbol(f"{contract.symbol}_{contract.currency}")
            elif contract.secType == "FUT":
                expiry = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
                instrument = normalize_instrument_symbol(f"{contract.symbol}-{expiry}" if expiry else contract.symbol)
            else:
                instrument = normalize_instrument_symbol(contract.symbol)
            pending.append(
                {
                    "id": str(row["orderId"]),
                    "instrument": instrument,
                    "state": row["state"],
                    "direction": "LONG" if getattr(row["order"], "action", "").upper() == "BUY" else "SHORT",
                    "order_type": getattr(row["order"], "orderType", None),
                    "units": float(getattr(row["order"], "totalQuantity", 0.0) or 0.0),
                }
            )
        return pending

    async def get_pending_orders(self) -> list[dict]:
        return await self._run_blocking(self._pending_orders_sync)

    def _resolve_contract_details_sync(self, contract):
        self._ensure_connected()
        state = _RequestState()
        self._app.contract_details_req = state
        req_id = int(time.time() * 1000) % 2_000_000_000
        self._app.reqContractDetails(req_id, contract)
        if not state.done.wait(timeout=10):
            self._app.contract_details_req = None
            raise RuntimeError("IBKR contract details request timed out")
        self._app.contract_details_req = None
        if state.error:
            raise RuntimeError(state.error)
        rows = state.rows
        if not rows:
            raise RuntimeError("IBKR contract details request returned no matches")
        return rows[0]["contract"]

    def _place_order_sync(self, request: "OrderRequest") -> tuple[str, float | None, str | None, str | None]:
        self._ensure_connected()
        order_id = self._next_order_id()
        contract = self._build_contract(request.instrument)
        if contract.secType == "FUT":
            expiry = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
            if len(expiry) == 6:
                contract = self._resolve_contract_details_sync(contract)
        order = Order()
        order.action = "BUY" if request.direction.value == "LONG" else "SELL"
        order.totalQuantity = abs(request.units)
        order.orderType = request.order_type.value
        order.tif = "GTC"
        # IB API defaults these legacy flags to True, but paper futures rejects them.
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        if request.order_type.value == "MARKET":
            order.orderType = "MKT"
        elif request.order_type.value == "LIMIT":
            order.orderType = "LMT"
            order.lmtPrice = float(request.limit_price)
        else:
            raise ValueError(f"Unsupported IBKR order type for phase 1: {request.order_type.value}")
        if self._account_id:
            order.account = self._account_id

        state = _OrderState()
        self._app.order_states[order_id] = state
        self._app.placeOrder(order_id, contract, order)

        if request.order_type.value == "MARKET":
            state.done.wait(timeout=10)

        if state.error:
            raise RuntimeError(state.error)

        fill_price = state.avg_fill_price if state.status == "Filled" else None
        trade_id = ibkr_position_id(request.instrument) if state.status == "Filled" else None

        stop_order_id = None
        if fill_price is not None and request.stop_loss is not None:
            stop_order_id = self._place_protective_stop_sync(
                contract=contract,
                direction=request.direction.value,
                units=abs(request.units),
                stop_price=float(request.stop_loss),
            )

        return str(order_id), fill_price, trade_id, stop_order_id

    async def place_order(
        self,
        order_id: uuid.UUID,
        request: "OrderRequest",
    ) -> tuple[str, float | None, str | None, str | None]:
        broker_order_id, fill_price, trade_id, stop_order_id = await self._run_blocking(self._place_order_sync, request)
        logger.info(
            "ibkr_order_placed",
            order_id=str(order_id),
            broker_order_id=broker_order_id,
            fill_price=fill_price,
            trade_id=trade_id,
            stop_order_id=stop_order_id,
            instrument=request.instrument,
        )
        return broker_order_id, fill_price, trade_id, stop_order_id

    def _place_protective_stop_sync(
        self,
        contract,
        direction: str,
        units: int,
        stop_price: float,
    ) -> str:
        stop_order_id = self._next_order_id()
        stop_order = Order()
        stop_order.action = "SELL" if direction == "LONG" else "BUY"
        stop_order.totalQuantity = abs(units)
        stop_order.orderType = "STP"
        stop_order.auxPrice = float(stop_price)
        stop_order.tif = "GTC"
        stop_order.eTradeOnly = False
        stop_order.firmQuoteOnly = False
        if self._account_id:
            stop_order.account = self._account_id

        state = _OrderState()
        self._app.order_states[stop_order_id] = state
        self._app.placeOrder(stop_order_id, contract, stop_order)
        logger.info(
            "ibkr_protective_stop_placed",
            stop_order_id=str(stop_order_id),
            instrument=getattr(contract, "localSymbol", None) or getattr(contract, "symbol", ""),
            stop_price=stop_price,
            units=units,
            direction=direction,
        )
        return str(stop_order_id)


    def _cancel_protective_stops_for_instrument_sync(self, instrument: str) -> list[str]:
        pending_orders = self._pending_orders_sync()
        cancelled: list[str] = []
        normalized = normalize_instrument_symbol(instrument)
        for order in pending_orders:
            if order.get("instrument") != normalized:
                continue
            if str(order.get("order_type", "")).upper() != "STP":
                continue
            broker_order_id = str(order.get("id"))
            self._app.cancelOrder(int(broker_order_id), "")
            cancelled.append(broker_order_id)
        return cancelled

    def _cancel_order_sync(self, broker_order_id: str) -> None:
        self._ensure_connected()
        self._app.cancelOrder(int(broker_order_id), "")

    async def cancel_order(self, broker_order_id: str) -> None:
        try:
            await self._run_blocking(self._cancel_order_sync, broker_order_id)
        except Exception as exc:
            logger.warning("ibkr_cancel_failed", broker_order_id=broker_order_id, error=str(exc))

    def _ensure_protective_stop_sync(self, trade_id: str, stop_price: float) -> dict:
        self._ensure_connected()
        instrument = normalize_instrument_symbol(trade_id)
        pending_orders = self._pending_orders_sync()
        existing_stop = next(
            (
                order for order in pending_orders
                if order.get("instrument") == instrument
                and str(order.get("order_type", "")).upper() == "STP"
            ),
            None,
        )
        if existing_stop is not None:
            return {
                "status": "exists",
                "trade_id": instrument,
                "stop_order_id": str(existing_stop.get("id")),
                "stop_price": stop_price,
            }

        positions = self._positions_sync()
        position = next((p for p in positions if p["id"] == instrument), None)
        if position is None:
            return {"status": "not_found", "trade_id": instrument, "stop_price": stop_price}

        current_units = float(position.get("currentUnits", 0.0) or 0.0)
        if abs(current_units) < 1e-9:
            return {"status": "flat", "trade_id": instrument, "stop_price": stop_price}

        contract = self._build_contract(position["instrument"])
        direction = "LONG" if current_units > 0 else "SHORT"
        stop_order_id = self._place_protective_stop_sync(
            contract=contract,
            direction=direction,
            units=abs(int(round(current_units))),
            stop_price=stop_price,
        )
        return {
            "status": "placed",
            "trade_id": instrument,
            "stop_order_id": stop_order_id,
            "stop_price": stop_price,
        }

    async def ensure_protective_stop(self, trade_id: str, stop_price: float) -> dict:
        return await self._run_blocking(self._ensure_protective_stop_sync, trade_id, stop_price)

    def _close_trade_sync(self, trade_id: str, units: str = "ALL") -> dict:
        self._ensure_connected()
        instrument = normalize_instrument_symbol(trade_id)
        cancelled_stop_ids = self._cancel_protective_stops_for_instrument_sync(instrument)
        positions = self._positions_sync()
        position = next((p for p in positions if p["id"] == instrument), None)
        if position is None:
            return {"status": "not_found", "trade_id": instrument, "cancelled_stop_ids": cancelled_stop_ids}

        current_units = float(position["currentUnits"])
        if abs(current_units) < 1e-9:
            return {"status": "flat", "trade_id": instrument, "cancelled_stop_ids": cancelled_stop_ids}

        close_qty = abs(current_units) if units == "ALL" else min(abs(current_units), abs(float(units)))
        contract = self._build_contract(position["instrument"])
        order_id = self._next_order_id()
        order = Order()
        order.action = "SELL" if current_units > 0 else "BUY"
        order.totalQuantity = close_qty
        order.orderType = "MKT"
        # IB API defaults these legacy flags to True, but paper futures rejects them.
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        if self._account_id:
            order.account = self._account_id

        state = _OrderState()
        self._app.order_states[order_id] = state
        self._app.placeOrder(order_id, contract, order)
        state.done.wait(timeout=10)

        return {
            "status": state.status or "submitted",
            "trade_id": instrument,
            "order_id": str(order_id),
            "avg_fill_price": state.avg_fill_price,
            "cancelled_stop_ids": cancelled_stop_ids,
        }

    async def close_trade(self, trade_id: str, units: str = "ALL") -> dict:
        return await self._run_blocking(self._close_trade_sync, trade_id, units)

    async def get_closed_trade(self, trade_id: str) -> dict | None:
        # Phase 1 leaves closed-trade reconciliation to DB-side closure logic.
        return None

    async def move_stop_loss_to_breakeven(self, trade_id: str, breakeven_price: float) -> None:
        logger.info(
            "ibkr_move_stop_loss_skipped",
            trade_id=trade_id,
            breakeven_price=breakeven_price,
            reason="phase_1_does_not_modify_attached_orders",
        )

    async def disconnect(self) -> None:
        if self._app.isConnected():
            await self._run_blocking(self._app.disconnect)
