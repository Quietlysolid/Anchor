"""Daily loss limit guard.

Halts trading for the rest of the calendar day (UTC) if realized
losses exceed the configured threshold.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict

import structlog

from anchor.config import settings

logger = structlog.get_logger(__name__)


class DailyLimiter:
    def __init__(self) -> None:
        # Tracks {date: realized_loss} in memory; reconciled from DB on startup
        self._daily_losses: Dict[date, float] = {}

    def record_trade(self, net_pl: float) -> None:
        """Call after every closed trade."""
        today = datetime.now(timezone.utc).date()
        self._daily_losses[today] = self._daily_losses.get(today, 0.0) + net_pl

    def is_halted(self, account_balance: float) -> bool:
        """Return True if trading should be halted for today."""
        today = datetime.now(timezone.utc).date()
        loss = self._daily_losses.get(today, 0.0)
        if loss >= 0:
            return False  # profitable day, never halt

        loss_pct = abs(loss) / account_balance
        threshold = settings.daily_loss_limit_pct  # default 0.03 (3%)

        if loss_pct >= threshold:
            logger.warning(
                "daily_loss_limit_hit",
                date=str(today),
                loss_pct=round(loss_pct * 100, 2),
                threshold_pct=round(threshold * 100, 2),
            )
            return True
        return False

    def get_today_loss(self) -> float:
        today = datetime.now(timezone.utc).date()
        return self._daily_losses.get(today, 0.0)

    def reset(self) -> None:
        """Clear history older than 7 days to prevent memory growth."""
        today = datetime.now(timezone.utc).date()
        self._daily_losses = {
            d: v for d, v in self._daily_losses.items()
            if (today - d).days <= 7
        }
