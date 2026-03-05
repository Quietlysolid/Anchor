"""
Drawdown circuit breaker.

Monitors account equity vs peak equity:
  - At reduce_pct drawdown → scale all positions by 50%
  - At halt_pct drawdown   → halt all new trading
"""
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class DrawdownMonitor:
    def __init__(self):
        self._peak_equity: float | None = None
        self._current_equity: float = 0.0
        self._halted: bool = False
        self._reduced: bool = False

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

    def check(self) -> tuple[bool, str | None]:
        """Returns (allowed: bool, reason: str | None)."""
        if self._halted:
            return False, f"DRAWDOWN_HALT:{self._compute_drawdown():.2%}"
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
