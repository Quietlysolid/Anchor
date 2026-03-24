"""
Position sizer.

Method: Fixed fractional (1% account risk per trade).
Optional: Kelly Criterion overlay (half-Kelly for safety).

Pip value calculation covers all 8 major instruments correctly:

  For USD-quoted pairs (EUR_USD, GBP_USD, AUD_USD, NZD_USD):
      1 pip move = pip_size USD per unit (direct)
      pip_value_per_unit = pip_size

  For USD-base pairs (USD_JPY, USD_CHF, USD_CAD):
      1 pip move = (pip_size / current_price) USD per unit
      pip_value_per_unit = pip_size / entry_price

  For cross pairs (EUR_JPY):
      1 pip move = (pip_size / entry_price) * 1 unit of quote
      Approximate in USD: pip_size / entry_price * usd_per_quote
      We approximate usd_per_quote ≈ 1.0 for JPY crosses
      (conservative over-estimate keeps us below true risk)

Formula:
  risk_amount = account_balance * risk_pct
  stop_pips = |entry - stop_loss| / pip_size
  units = risk_amount / (stop_pips * pip_value_per_unit)
"""
import structlog

from anchor.config import get_settings
from anchor.utils.math_utils import get_pip_size

logger = structlog.get_logger(__name__)
settings = get_settings()
MICRO_LOT = 1_000   # OANDA minimum unit

# Instruments where USD is the base currency (not the quote)
USD_BASE_PAIRS = {"USD_JPY", "USD_CHF", "USD_CAD"}

# Instruments quoted in JPY (need extra conversion approximation)
JPY_QUOTE_PAIRS = {"EUR_JPY", "GBP_JPY", "AUD_JPY", "NZD_JPY"}

# GBP-quoted pairs: 1 pip = pip_size GBP → approximate in USD via GBP/USD
GBP_QUOTE_PAIRS = {"EUR_GBP"}

# Approximate rates for cross-pair pip conversion (conservative — updated at runtime if possible)
_APPROX_USDJPY = 150.0
_APPROX_GBPUSD = 1.27  # conservative (low end) — over-estimates risk slightly, keeps us safe


def set_usdjpy_rate(rate: float) -> None:
    """Update the live USD/JPY rate for accurate cross-pair sizing."""
    global _APPROX_USDJPY
    if rate > 0:
        _APPROX_USDJPY = rate


def _pip_value_per_unit(instrument: str, entry_price: float, pip_size: float) -> float:
    """Return USD value of one pip per one unit of the instrument."""
    if instrument in USD_BASE_PAIRS:
        # e.g. USD_JPY: 1 pip = pip_size / entry_price USD
        return pip_size / entry_price if entry_price > 0 else pip_size

    if instrument in JPY_QUOTE_PAIRS:
        # e.g. EUR_JPY: 1 pip in JPY, convert to USD
        return (pip_size / _APPROX_USDJPY) if _APPROX_USDJPY > 0 else pip_size

    if instrument in GBP_QUOTE_PAIRS:
        # e.g. EUR_GBP: 1 pip in GBP, convert to USD via GBP/USD rate
        return pip_size * _APPROX_GBPUSD if _APPROX_GBPUSD > 0 else pip_size

    # Default: USD is the quote currency (EUR_USD, GBP_USD, AUD_USD, NZD_USD, USD_CHF treated above)
    return pip_size


class PositionSizer:
    def compute(
        self,
        account_balance:   float,
        instrument:        str,
        entry_price:       float,
        stop_loss:         float,
        risk_pct_override: float | None = None,
        kelly_fraction:    float | None = None,
        correlation_scale: float = 1.0,
        drawdown_scale:    float = 1.0,
        vix_scale:         float = 1.0,
        news_scale:        float = 1.0,
        session_scale:     float = 1.0,
        current_atr:       float | None = None,
        reference_atr:     float | None = None,
        rolling_score_scale: float = 1.0,
        regime_scale:      float = 1.0,
        positioning_scale: float = 1.0,
    ) -> int:
        """
        Returns position size in units (OANDA native).
        Always a multiple of MICRO_LOT (1,000 units).

        vix_scale: VIX position-size multiplier from vix_filter.get_cached_multiplier()
            [0.25, 1.0] — reduces size when VIX is elevated (>20) to protect capital
            in high-fear regimes where FX spreads widen and edge degrades.

        news_scale: 0.5 when a MEDIUM-impact event is within ±1h; 1.0 otherwise.
            Trades near medium-impact events are allowed but sized down to reduce
            event-risk exposure without fully suppressing the signal.

        session_scale: Tier 1 session quality multiplier from Redis key 'session_quality'.
            TRENDING=1.0, MIXED=0.85, CHOPPY=0.65. Defaults to 1.0 when key is absent.

        rolling_score_scale: Tier 2b rolling session score multiplier.
            0.75 when the 5-session rolling average drops below 5.0 (out of 10).
            1.0 otherwise. Reset to 1.0 each Sunday after weekly synthesis.

        regime_scale: market regime multiplier derived from HMM/ATR regime classifier.
            Trend-following (London/M15): TRENDING=1.0, RANGING=0.80, VOLATILE=0.60.
            Mean-reversion (LCR/MR):     RANGING=1.0,  TRENDING=0.80, VOLATILE=0.60.
            Clamped to [0.50, 1.0]. Defaults to 1.0 when regime is UNKNOWN or unavailable.

        current_atr / reference_atr: when both are provided, applies
        volatility-regime normalization = reference_atr / current_atr,
        capped at [0.5, 1.5]. This prevents high-vol environments from
        silently expanding dollar risk through wider ATR-based stops while
        also allowing modest size increases in compressed low-vol regimes.
        reference_atr should be the trailing 20-day median ATR.
        """
        pip_size = get_pip_size(instrument)
        stop_distance = abs(entry_price - stop_loss)

        if stop_distance < 1e-10:
            logger.warning(
                "position_sizer_zero_stop_fallback",
                instrument=instrument,
                entry_price=entry_price,
                stop_loss=stop_loss,
            )
            return MICRO_LOT

        stop_pips = stop_distance / pip_size
        pv_per_unit = _pip_value_per_unit(instrument, entry_price, pip_size)

        risk_pct = risk_pct_override if risk_pct_override is not None else settings.max_risk_per_trade
        risk_amount = account_balance * risk_pct
        units_raw   = risk_amount / (stop_pips * pv_per_unit)

        # Kelly overlay (half-Kelly cap) — only reduces, never increases
        if kelly_fraction is not None and 0 < kelly_fraction < 1.0:
            kelly_units = units_raw * kelly_fraction * 0.5
            units_raw   = min(units_raw, kelly_units)

        # Volatility-regime normalization: scale down in high-vol, up in low-vol
        vol_scale = 1.0
        if current_atr is not None and reference_atr is not None:
            if current_atr > 1e-10 and reference_atr > 1e-10:
                vol_scale = max(0.5, min(1.5, reference_atr / current_atr))

        # Scale factors (correlation, drawdown, VIX, news proximity, session quality,
        # rolling score, volatility regime, market regime, positioning crowding)
        vix_scale           = max(0.25, min(1.0, vix_scale))           # clamp to safe range
        news_scale          = max(0.25, min(1.0, news_scale))           # clamp to safe range
        session_scale       = max(0.50, min(1.0, session_scale))        # clamp to safe range
        rolling_score_scale = max(0.75, min(1.0, rolling_score_scale))  # clamp: floor 0.75, no upside
        regime_scale        = max(0.50, min(1.0, regime_scale))         # clamp: floor 0.50, never upsize
        positioning_scale   = max(0.50, min(1.0, positioning_scale))    # clamp: reduce-only
        units_scaled = units_raw * correlation_scale * drawdown_scale * vix_scale * news_scale * session_scale * rolling_score_scale * vol_scale * regime_scale * positioning_scale

        # Snap to micro-lot boundary
        units = int(units_scaled // MICRO_LOT) * MICRO_LOT

        # Hard cap: never more than 5% of account value in a single position
        if entry_price > 0 and pv_per_unit > 0:
            notional_per_unit = entry_price  # approximate
            max_notional = account_balance * settings.max_position_pct
            max_units_by_notional = int(max_notional / notional_per_unit)
            max_units_by_notional = (max_units_by_notional // MICRO_LOT) * MICRO_LOT
            units = min(units, max(max_units_by_notional, MICRO_LOT))

        final = max(units, MICRO_LOT)
        logger.debug(
            "position_sized",
            instrument=instrument,
            units=final,
            risk_pct=round(risk_pct * 100, 2),
            stop_pips=round(stop_pips, 1),
            vol_scale=round(vol_scale, 3),
            vix_scale=round(vix_scale, 3),
            drawdown_scale=round(drawdown_scale, 3),
            session_scale=round(session_scale, 3),
            regime_scale=round(regime_scale, 3),
            positioning_scale=round(positioning_scale, 3),
        )
        return final

    def compute_units(
        self,
        account_balance: float,
        stop_distance: float,
        instrument: str,
        entry_price: float = 1.0,
    ) -> int:
        """Convenience method used by the backtesting engine.

        When entry_price is not available (e.g., before fill), caller passes
        a reference price (e.g., current close) as entry_price.
        """
        if stop_distance < 1e-10:
            return MICRO_LOT

        pip_size = get_pip_size(instrument)
        stop_pips = stop_distance / pip_size
        pv_per_unit = _pip_value_per_unit(instrument, entry_price, pip_size)

        risk_amount = account_balance * settings.max_risk_per_trade
        units_raw = risk_amount / (stop_pips * pv_per_unit)

        units = int(units_raw // MICRO_LOT) * MICRO_LOT
        return max(units, MICRO_LOT)
