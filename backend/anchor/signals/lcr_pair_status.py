"""
LCR pair-level status evaluation.

Pure functions and one async loader — single source of truth for both the
API endpoint and signal_tasks.py.

Pure evaluation: pass PairMetrics in, get PairStatusResult back.
DB loader: load_lcr_pair_statuses(session) → dict[instrument, PairStatusResult].

All thresholds are module-level constants; change them here only.

Statuses
--------
active    — no flags; trade normally
watchlist — at least one soft flag; trade normally but monitor closely
disabled  — at least one hard flag; skip execution until manually cleared

Disable rules (any one is sufficient)
--------------------------------------
1. fragile robustness AND spread breach
   (combined-stress PF < 1.0) AND (realized spread > 2x modeled, ≥ 20 obs)
2. 15+ live trades with 0 wins
3. ≥ 80% of portfolio gross losses concentrated in this pair, with 0 wins

Watchlist rules (any one is sufficient; escalates to disable if combined)
--------------------------------------------------------------------------
1. combined-stress PF < 1.0 alone
2. realized spread > 2x modeled alone (≥ 20 obs required)
3. zero entries after 60 calendar days from system live date
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ── Robustness snapshot (locked 2026-03-23, pre-live-trades) ─────────────────
# Source: lcr_robustness_snapshot_2026-03-23.txt  scenario=spread2x_slip1pip
ROBUSTNESS_COMBINED_PF: dict[str, float] = {
    "EUR_USD": 1.214,
    "NZD_USD": 1.142,
    "AUD_USD": 0.992,
    "EUR_JPY": 0.966,
    "USD_CAD": 0.933,
}

# Modeled full-spread per trade (source: lcr_backtest.py _SPREAD_COST)
MODELED_SPREAD: dict[str, float] = {
    "EUR_USD": 0.00002,
    "NZD_USD": 0.00015,
    "AUD_USD": 0.00012,
    "EUR_JPY": 0.030,
    "USD_CAD": 0.00015,
}

# ── Decision thresholds ───────────────────────────────────────────────────────
FRAGILE_COMBINED_PF        = 1.0   # robustness combined PF below this → fragile flag
SPREAD_MULTIPLE_THRESHOLD  = 2.0   # realized > N × modeled → spread breach flag
SPREAD_OBS_MINIMUM         = 20    # spread breach requires at least this many observations
ZERO_WIN_LOSS_THRESHOLD    = 15    # N+ losses with 0 wins → disable
LOSS_CONCENTRATION_PCT     = 0.80  # ≥ 80 % of portfolio gross losses + 0 wins → disable
ZERO_ENTRY_DAYS_THRESHOLD  = 60    # 0 entries after N calendar days → watchlist


class PairStatus(str, Enum):
    ACTIVE    = "active"
    WATCHLIST = "watchlist"
    DISABLED  = "disabled"


@dataclass
class PairMetrics:
    instrument: str
    live_trades: int       = 0
    live_wins: int         = 0
    live_losses: int       = 0
    # gross loss for this pair (absolute value, dollars)
    pair_gross_loss: float = 0.0
    # gross loss across entire LCR portfolio (absolute value, dollars)
    portfolio_gross_loss: float = 0.0
    # calendar days since the LCR system first went live
    days_since_live: int   = 0
    # mean realized spread (same units as MODELED_SPREAD); None if not measured
    realized_spread_mean: Optional[float] = None
    spread_obs_count: int  = 0


@dataclass
class PairStatusResult:
    instrument: str
    status: PairStatus
    reasons: list[str]     = field(default_factory=list)
    metrics: dict          = field(default_factory=dict)


def evaluate_pair_status(m: PairMetrics) -> PairStatusResult:
    """
    Evaluate LCR pair status from a PairMetrics snapshot.

    Pure function. Deterministic. No database access.
    Returns PairStatusResult with status, machine-readable reasons[], and metrics snapshot.
    """
    combined_pf = ROBUSTNESS_COMBINED_PF.get(m.instrument)
    modeled_spread = MODELED_SPREAD.get(m.instrument)

    # ── Condition flags ───────────────────────────────────────────────────────
    fragile = combined_pf is not None and combined_pf < FRAGILE_COMBINED_PF

    spread_breach = (
        m.spread_obs_count >= SPREAD_OBS_MINIMUM
        and m.realized_spread_mean is not None
        and modeled_spread is not None
        and m.realized_spread_mean > modeled_spread * SPREAD_MULTIPLE_THRESHOLD
    )

    zero_win_floor_hit = m.live_losses >= ZERO_WIN_LOSS_THRESHOLD and m.live_wins == 0

    loss_concentrated = (
        m.live_wins == 0
        and m.portfolio_gross_loss > 0
        and (m.pair_gross_loss / m.portfolio_gross_loss) >= LOSS_CONCENTRATION_PCT
    )

    zero_entries_late = (
        m.live_trades == 0
        and m.days_since_live >= ZERO_ENTRY_DAYS_THRESHOLD
    )

    # ── Disable rules (hard gates) ────────────────────────────────────────────
    disable_reasons: list[str] = []

    if fragile and spread_breach:
        disable_reasons.append("FRAGILE_ROBUSTNESS_AND_SPREAD_BREACH")

    if zero_win_floor_hit:
        disable_reasons.append(f"ZERO_WINS_AFTER_{m.live_losses}_LOSSES")

    if loss_concentrated:
        pct = round(m.pair_gross_loss / m.portfolio_gross_loss * 100, 1)
        disable_reasons.append(f"LOSS_CONCENTRATION_{pct}pct_ZERO_WINS")

    if disable_reasons:
        return PairStatusResult(
            instrument=m.instrument,
            status=PairStatus.DISABLED,
            reasons=disable_reasons,
            metrics=_metrics_snapshot(m, combined_pf, modeled_spread),
        )

    # ── Watchlist rules (soft flags) ──────────────────────────────────────────
    watch_reasons: list[str] = []

    if fragile:
        watch_reasons.append("FRAGILE_ROBUSTNESS")

    if spread_breach:
        watch_reasons.append("SPREAD_BREACH")

    if zero_entries_late:
        watch_reasons.append(f"ZERO_ENTRIES_AFTER_{m.days_since_live}_DAYS")

    if watch_reasons:
        return PairStatusResult(
            instrument=m.instrument,
            status=PairStatus.WATCHLIST,
            reasons=watch_reasons,
            metrics=_metrics_snapshot(m, combined_pf, modeled_spread),
        )

    return PairStatusResult(
        instrument=m.instrument,
        status=PairStatus.ACTIVE,
        reasons=[],
        metrics=_metrics_snapshot(m, combined_pf, modeled_spread),
    )


def _metrics_snapshot(
    m: PairMetrics,
    combined_pf: float | None,
    modeled_spread: float | None,
) -> dict:
    return {
        "live_trades":          m.live_trades,
        "live_wins":            m.live_wins,
        "live_losses":          m.live_losses,
        "days_since_live":      m.days_since_live,
        "robustness_combined_pf": combined_pf,
        "modeled_spread":       modeled_spread,
        "realized_spread_mean": m.realized_spread_mean,
        "spread_obs_count":     m.spread_obs_count,
    }


# ── I/O layer ─────────────────────────────────────────────────────────────────

# Hardcoded LCR live date — the day the first LCR signal was eligible to fire.
# Do NOT derive this from MIN(opened_at): other strategy trades (London trend)
# may predate LCR, which would make the 60-day zero-entries watchlist fire too early.
LCR_LIVE_DATE = date(2026, 3, 17)


async def load_lcr_pair_statuses(session: "AsyncSession") -> dict[str, PairStatusResult]:
    """
    Query the trades table, build PairMetrics for each LCR pair, and evaluate.

    Single source of truth used by both /system/lcr-pair-status and signal_tasks.py.
    Any change to aggregation logic belongs here, not in the callers.
    """
    from sqlalchemy import text
    from anchor.signals.london_close_reversion import LCR_INSTRUMENTS

    lcr_instruments = list(LCR_INSTRUMENTS)
    days_since_live = max(0, (datetime.now(timezone.utc).date() - LCR_LIVE_DATE).days)

    rows = (await session.execute(
        text(
            "SELECT instrument, net_pl, spread_at_fill "
            "FROM trades WHERE instrument = ANY(:pairs)"
        ),
        {"pairs": lcr_instruments},
    )).fetchall()

    pair_stats: dict[str, dict] = {
        pair: {"trades": 0, "wins": 0, "losses": 0, "gross_loss": 0.0, "spreads": []}
        for pair in lcr_instruments
    }

    for r in rows:
        inst, net_pl = r[0], float(r[1])
        spread = float(r[2]) if r[2] is not None else None
        if inst not in pair_stats:
            continue
        s = pair_stats[inst]
        s["trades"] += 1
        if net_pl > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1
            s["gross_loss"] += abs(net_pl)
        if spread is not None:
            s["spreads"].append(spread)

    portfolio_gross_loss = sum(s["gross_loss"] for s in pair_stats.values())

    results: dict[str, PairStatusResult] = {}
    for pair in lcr_instruments:
        s = pair_stats[pair]
        spreads = s["spreads"]
        metrics = PairMetrics(
            instrument=pair,
            live_trades=s["trades"],
            live_wins=s["wins"],
            live_losses=s["losses"],
            pair_gross_loss=s["gross_loss"],
            portfolio_gross_loss=portfolio_gross_loss,
            days_since_live=days_since_live,
            realized_spread_mean=sum(spreads) / len(spreads) if spreads else None,
            spread_obs_count=len(spreads),
        )
        results[pair] = evaluate_pair_status(metrics)

    return results
