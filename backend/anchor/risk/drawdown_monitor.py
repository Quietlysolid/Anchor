"""
Drawdown circuit breaker.

Monitors account equity vs peak equity:
  - At reduce_pct drawdown → scale all positions by 50%
  - At halt_pct drawdown   → halt all new trading

Also tracks a monthly circuit breaker (resets on the 1st of each month):
  - If month-to-date loss exceeds monthly_halt_pct → halt until next month
"""
import structlog
from datetime import datetime, timezone

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class DrawdownMonitor:
    def __init__(self):
        self._peak_equity: float | None = None
        self._current_equity: float = 0.0
        self._halted: bool = False
        self._reduced: bool = False
        # Monthly circuit breaker state
        self._month_start_equity: float | None = None
        self._current_month: str | None = None  # "YYYY-MM"
        self._monthly_halted: bool = False

    async def bootstrap_peak_equity(self, session) -> None:
        """Load historical peak equity from the DB so worker restarts don't reset the circuit breaker.

        Call once during startup before the first update(). Safe to call multiple times.
        """
        try:
            from anchor.database.repositories.equity import EquityRepository
            repo = EquityRepository(session)
            latest = await repo.get_latest()
            if latest and latest.peak_equity:
                db_peak = float(latest.peak_equity)
                if self._peak_equity is None or db_peak > self._peak_equity:
                    self._peak_equity = db_peak
                    logger.info("drawdown_peak_bootstrapped", peak_equity=db_peak)
        except Exception as exc:
            logger.warning("drawdown_bootstrap_failed", error=str(exc))

    async def bootstrap_month_state(self, session, current_equity: float | None = None) -> None:
        """Load current-month baseline from DB so restarts do not clear monthly halt state."""
        try:
            from datetime import datetime, timezone
            from anchor.database.models import EquityCurvePoint
            from sqlalchemy import select

            now = datetime.now(timezone.utc)
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            current_month = now.strftime("%Y-%m")

            row = await session.execute(
                select(EquityCurvePoint)
                .where(EquityCurvePoint.time >= month_start)
                .order_by(EquityCurvePoint.time.asc())
                .limit(1)
            )
            first_point = row.scalar_one_or_none()
            baseline = None
            if first_point is not None:
                baseline = float(first_point.account_equity)
            elif current_equity is not None:
                baseline = float(current_equity)

            if baseline is None:
                return

            self._current_month = current_month
            self._month_start_equity = baseline
            self._monthly_halted = False

            if current_equity is not None and baseline > 0:
                mtd_loss = (baseline - float(current_equity)) / baseline
                if mtd_loss >= settings.monthly_halt_pct:
                    self._monthly_halted = True

            logger.info(
                "monthly_circuit_breaker_bootstrapped",
                month=current_month,
                month_start_equity=baseline,
                monthly_halted=self._monthly_halted,
            )
        except Exception as exc:
            logger.warning("monthly_circuit_bootstrap_failed", error=str(exc))

    def update(self, equity: float) -> None:
        self._current_equity = equity
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity

        drawdown = self._compute_drawdown()

        if drawdown >= settings.drawdown_halt_pct:
            if not self._halted:
                logger.warning("drawdown_halt_triggered", drawdown=drawdown)
            self._halted  = True
            self._reduced = True
        elif drawdown >= settings.drawdown_reduce_pct:
            if not self._reduced:
                logger.warning("drawdown_reduce_triggered", drawdown=drawdown)
            self._halted  = False
            self._reduced = True
        else:
            self._halted  = False
            self._reduced = False

        # Monthly circuit breaker — resets on first update of each new month
        now_month = datetime.now(timezone.utc).strftime("%Y-%m")
        if self._current_month != now_month:
            self._current_month    = now_month
            self._month_start_equity = equity
            self._monthly_halted   = False
            logger.info("monthly_circuit_breaker_reset", month=now_month, equity=equity)

        if self._month_start_equity and self._month_start_equity > 0:
            mtd_loss = (self._month_start_equity - equity) / self._month_start_equity
            if mtd_loss >= settings.monthly_halt_pct:
                if not self._monthly_halted:
                    logger.warning(
                        "monthly_halt_triggered",
                        month=now_month,
                        mtd_loss=f"{mtd_loss:.2%}",
                        limit=f"{settings.monthly_halt_pct:.2%}",
                    )
                self._monthly_halted = True

    def check(self) -> tuple[bool, str | None]:
        """Returns (allowed: bool, reason: str | None)."""
        if self._halted:
            return False, f"DRAWDOWN_HALT:{self._compute_drawdown():.2%}"
        if self._monthly_halted:
            return False, "MONTHLY_DRAWDOWN_HALT"
        return True, None

    @property
    def scale_factor(self) -> float:
        """Returns 0.5 if in reduced mode, 1.0 otherwise."""
        return 0.5 if self._reduced else 1.0

    @property
    def current_drawdown(self) -> float:
        return self._compute_drawdown()

    def _compute_drawdown(self) -> float:
        if self._peak_equity is None or self._peak_equity == 0:
            return 0.0
        return (self._peak_equity - self._current_equity) / self._peak_equity
