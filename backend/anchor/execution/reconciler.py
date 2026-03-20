"""
Position reconciler.
Every 15 minutes: compare DB open positions vs OANDA ground truth.
On VPS restart: full state reconstruction from broker.
"""
from datetime import timedelta

import structlog

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
                # Try to recover signal_id from the order that opened this trade
                signal_id = None
                opening_order_id = trade.get("openingOrderID")
                if opening_order_id:
                    linked_order = await self.order_repo.get_by_oanda_id(opening_order_id)
                    if linked_order and linked_order.signal_id:
                        signal_id = linked_order.signal_id
                        await self.order_repo.update_state(linked_order.id, "FILLED", {"filled_by_reconciler": True})

                await self.pos_repo.create_from_broker_trade(trade, signal_id=signal_id)
                result["missing_from_db"].append(trade["id"])
                result["actions_taken"].append(f"RECONSTRUCTED:{trade['id']}")
                logger.warning("position_missing_from_db", trade_id=trade["id"], signal_linked=signal_id is not None)

        # Cancel stale pending orders
        stale_cutoff = utcnow() - timedelta(hours=STALE_ORDER_HOURS)
        stale_orders = await self.order_repo.get_stale_pending(stale_cutoff)
        for order in stale_orders:
            if order.oanda_order_id:
                await self.broker.cancel_order(order.oanda_order_id)
            await self.order_repo.update_state(order.id, "EXPIRED", {"reason": "STALE"})
            result["actions_taken"].append(f"CANCELLED_STALE:{order.id}")

        await self.event_repo.insert(
            event_type="RECONCILIATION",
            severity="INFO" if not result["missing_from_broker"] and not result["missing_from_db"] else "WARN",
            component="RECONCILER",
            message=f"Reconciled: {len(result['actions_taken'])} actions",
            metadata=result,
        )

        return result
