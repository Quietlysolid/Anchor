"""
Position reconciler.
Every 15 minutes: compare DB open positions vs OANDA ground truth.
On VPS restart: full state reconstruction from broker.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy import text as _sql_text
from sqlalchemy import select as _select

from anchor.database.models import Fill, Position, Trade
from anchor.utils.math_utils import get_pip_size
from anchor.utils.time_utils import utcnow

logger = structlog.get_logger(__name__)

STALE_ORDER_HOURS = 4


class Reconciler:
    def __init__(self, broker_client, position_repo, order_repo, system_event_repo, daily_limiter=None):
        self.broker         = broker_client
        self.pos_repo       = position_repo
        self.order_repo     = order_repo
        self.event_repo     = system_event_repo
        self.daily_limiter  = daily_limiter

    @staticmethod
    def _parse_broker_time(value: str | None) -> datetime:
        if not value:
            return utcnow()
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return utcnow()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def _direction_from_trade(trade: dict) -> str:
        units = float(trade.get("currentUnits", trade.get("initialUnits", 0)) or 0)
        return "LONG" if units > 0 else "SHORT"

    async def _match_order_for_trade(self, trade: dict):
        opening_order_id = trade.get("openingOrderID")
        if opening_order_id:
            linked = await self.order_repo.get_by_oanda_id(opening_order_id)
            if linked:
                return linked

        instrument = trade.get("instrument", "UNKNOWN")
        direction = self._direction_from_trade(trade)
        opened_at = self._parse_broker_time(trade.get("openTime"))
        candidates = await self.order_repo.get_fill_match_candidates(
            instrument=instrument,
            direction=direction,
            opened_after=opened_at - timedelta(hours=6),
            opened_before=opened_at + timedelta(minutes=5),
            limit=10,
        )
        if not candidates:
            return None

        # Prefer signal-linked orders and the closest created_at to broker openTime.
        candidates.sort(
            key=lambda order: (
                order.signal_id is None,
                abs((order.created_at - opened_at).total_seconds()) if order.created_at else float("inf"),
            )
        )
        return candidates[0]

    async def _ensure_fill_audit(self, order, trade: dict) -> None:
        existing_fills = await self.order_repo.get_fills(order.id)
        if existing_fills:
            return

        fill_price = float(trade.get("price", 0) or 0)
        benchmark = float(order.limit_price) if order.limit_price is not None else fill_price
        pip_size = get_pip_size(order.instrument)
        slippage_pips = None
        if pip_size > 0:
            if order.direction == "LONG":
                slippage_pips = round((fill_price - benchmark) / pip_size, 4)
            else:
                slippage_pips = round((benchmark - fill_price) / pip_size, 4)

        fill = Fill(
            order_id=order.id,
            instrument=order.instrument,
            units_filled=Decimal(str(abs(float(trade.get("initialUnits", trade.get("currentUnits", 0)) or 0)))),
            fill_price=Decimal(str(fill_price)),
            fill_at=self._parse_broker_time(trade.get("openTime")),
            expected_price=Decimal(str(benchmark)) if benchmark else None,
            slippage_pips=Decimal(str(slippage_pips)) if slippage_pips is not None else None,
            spread_at_fill=None,
            oanda_fill_id=None,
            metadata_={"reconstructed_from_broker": True, "oanda_trade_id": trade.get("id")},
        )
        await self.order_repo.insert_fill(fill)

    async def _backfill_recent_closed_execution_audit(self, result: dict) -> None:
        """Repair signal/fill linkage for recently closed reconciled trades.

        This keeps live audit integrity intact even when the worker only learns
        about a fill after the position is already open at OANDA.
        """
        session = self.pos_repo.session
        cutoff = utcnow() - timedelta(days=30)
        rows = (
            await session.execute(
                _select(Position, Trade)
                .join(Trade, Trade.position_id == Position.id)
                .where(
                    Position.closed_at.is_not(None),
                    Position.closed_at >= cutoff,
                )
                .order_by(Position.closed_at.desc())
            )
        ).all()

        for position, trade in rows:
            needs_signal = position.signal_id is None or trade.signal_id is None
            matched_order = None
            if needs_signal:
                matched_order = await self._match_order_for_trade(
                    {
                        "id": position.oanda_trade_id,
                        "instrument": position.instrument,
                        "initialUnits": str(position.units if position.direction == "LONG" else -position.units),
                        "price": str(position.avg_entry_price),
                        "openTime": position.opened_at.isoformat() if position.opened_at else None,
                    }
                )
                if matched_order and matched_order.signal_id:
                    position.signal_id = matched_order.signal_id
                    trade.signal_id = matched_order.signal_id

            if matched_order and matched_order.state != "FILLED":
                await self.order_repo.update_state(
                    matched_order.id,
                    "FILLED",
                    {"filled_by_reconciler": True, "oanda_trade_id": position.oanda_trade_id},
                )

            if matched_order:
                await self._ensure_fill_audit(
                    matched_order,
                    {
                        "id": position.oanda_trade_id,
                        "instrument": position.instrument,
                        "initialUnits": str(position.units if position.direction == "LONG" else -position.units),
                        "price": str(position.avg_entry_price),
                        "openTime": position.opened_at.isoformat() if position.opened_at else None,
                    },
                )

            if matched_order and (needs_signal or matched_order.state != "FILLED"):
                result["actions_taken"].append(f"REPAIRED_AUDIT:{position.oanda_trade_id}")

        audit_gaps = (
            await session.execute(
                _sql_text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE t.signal_id IS NULL)::int AS missing_trade_signal,
                        COUNT(*) FILTER (WHERE p.signal_id IS NULL)::int AS missing_position_signal,
                        COUNT(*) FILTER (
                            WHERE NOT EXISTS (
                                SELECT 1
                                FROM orders o
                                JOIN fills f ON f.order_id = o.id
                                WHERE o.signal_id = COALESCE(t.signal_id, p.signal_id)
                            )
                        )::int AS missing_fill_audit
                    FROM positions p
                    JOIN trades t ON t.position_id = p.id
                    WHERE p.closed_at >= :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
        ).mappings().first()
        result["audit_gaps"] = dict(audit_gaps) if audit_gaps else {
            "missing_trade_signal": 0,
            "missing_position_signal": 0,
            "missing_fill_audit": 0,
        }

    async def reconcile(self) -> dict:
        """
        Compare DB positions vs broker positions.
        Returns dict with discrepancies found and actions taken.
        """
        result = {
            "timestamp":          utcnow().isoformat(),
            "db_positions":       0,
            "broker_positions":   0,
            "missing_from_db":    [],
            "missing_from_broker": [],
            "actions_taken":      [],
        }

        broker_trades  = await self.broker.get_open_trades()
        broker_ids     = {t["id"] for t in broker_trades}

        db_positions = await self.pos_repo.get_open_positions()
        db_trade_ids = {p.oanda_trade_id for p in db_positions if p.oanda_trade_id}

        result["db_positions"]     = len(db_positions)
        result["broker_positions"] = len(broker_trades)

        # DB has position, broker doesn't → fetch closed trade details and mark closed
        for pos in db_positions:
            if pos.oanda_trade_id and pos.oanda_trade_id not in broker_ids:
                # Try to get the closed trade details from OANDA for accurate P&L
                realized_pl = 0.0
                exit_price  = None
                close_reason = "SL_TP_OR_MANUAL"
                try:
                    closed = await self.broker.get_closed_trade(pos.oanda_trade_id)
                    if closed:
                        realized_pl  = float(closed.get("realizedPL", 0.0))
                        exit_price   = float(closed.get("averageClosePrice", 0)) or None
                        close_reason = "SL_TP_OR_MANUAL" if closed.get("closingTransactionIDs") else "MANUAL"
                except Exception as exc:
                    logger.warning("reconciler_get_closed_trade_failed", trade_id=pos.oanda_trade_id, error=str(exc))

                await self.pos_repo.mark_closed(
                    position_id=pos.id,
                    close_reason=close_reason,
                    closed_at=utcnow(),
                    realized_pl=realized_pl,
                    exit_price=exit_price,
                )

                # Record realized P&L so daily loss limit gate stays current
                if self.daily_limiter is not None:
                    self.daily_limiter.record_trade(realized_pl)

                result["missing_from_broker"].append(pos.oanda_trade_id)
                result["actions_taken"].append(f"CLOSED_IN_DB:{pos.oanda_trade_id}")
                logger.info(
                    "position_closed_by_broker",
                    trade_id=pos.oanda_trade_id,
                    realized_pl=realized_pl,
                    close_reason=close_reason,
                )

        # Broker has trade, DB doesn't → reconstruct
        for trade in broker_trades:
            if trade["id"] not in db_trade_ids:
                linked_order = await self._match_order_for_trade(trade)
                signal_id = linked_order.signal_id if linked_order and linked_order.signal_id else None
                if linked_order:
                    await self.order_repo.update_state(
                        linked_order.id,
                        "FILLED",
                        {"filled_by_reconciler": True, "oanda_trade_id": trade["id"]},
                    )
                    await self._ensure_fill_audit(linked_order, trade)

                await self.pos_repo.create_from_broker_trade(trade, signal_id=signal_id)
                result["missing_from_db"].append(trade["id"])
                result["actions_taken"].append(f"RECONSTRUCTED:{trade['id']}")
                logger.warning(
                    "position_missing_from_db",
                    trade_id=trade["id"],
                    signal_linked=signal_id is not None,
                    order_linked=linked_order is not None,
                )

        # Cancel stale pending orders
        stale_cutoff = utcnow() - timedelta(hours=STALE_ORDER_HOURS)
        stale_orders = await self.order_repo.get_stale_pending(stale_cutoff)
        for order in stale_orders:
            if order.oanda_order_id:
                await self.broker.cancel_order(order.oanda_order_id)
            await self.order_repo.update_state(order.id, "EXPIRED", {"reason": "STALE"})
            result["actions_taken"].append(f"CANCELLED_STALE:{order.id}")

        await self._backfill_recent_closed_execution_audit(result)

        await self.event_repo.insert(
            event_type="RECONCILIATION",
            severity=(
                "INFO"
                if (
                    not result["missing_from_broker"]
                    and not result["missing_from_db"]
                    and not any(result.get("audit_gaps", {}).values())
                )
                else "WARN"
            ),
            component="RECONCILER",
            message=f"Reconciled: {len(result['actions_taken'])} actions",
            metadata=result,
        )

        return result
