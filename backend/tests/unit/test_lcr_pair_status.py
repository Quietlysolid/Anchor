"""
Unit tests for LCR pair-level status evaluation.

All tests use the pure evaluate_pair_status() function — no DB, no I/O.
"""
import pytest

from anchor.signals.lcr_pair_status import (
    PairMetrics,
    PairStatus,
    PairStatusResult,
    evaluate_pair_status,
    ROBUSTNESS_COMBINED_PF,
    MODELED_SPREAD,
    FRAGILE_COMBINED_PF,
    SPREAD_MULTIPLE_THRESHOLD,
    SPREAD_OBS_MINIMUM,
    ZERO_WIN_LOSS_THRESHOLD,
    LOSS_CONCENTRATION_PCT,
    ZERO_ENTRY_DAYS_THRESHOLD,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _active(instrument: str = "EUR_USD", **kwargs) -> PairMetrics:
    """Minimal healthy metrics — should always produce ACTIVE."""
    return PairMetrics(instrument=instrument, days_since_live=10, **kwargs)


def _fragile_instrument() -> str:
    """Return an instrument known to have combined PF < 1.0."""
    return next(i for i, pf in ROBUSTNESS_COMBINED_PF.items() if pf < FRAGILE_COMBINED_PF)


def _robust_instrument() -> str:
    """Return an instrument known to have combined PF >= 1.0."""
    return next(i for i, pf in ROBUSTNESS_COMBINED_PF.items() if pf >= FRAGILE_COMBINED_PF)


# ── active (baseline) ─────────────────────────────────────────────────────────

class TestActive:
    def test_healthy_pair_is_active(self):
        r = evaluate_pair_status(_active())
        assert r.status == PairStatus.ACTIVE
        assert r.reasons == []

    def test_robust_instrument_with_trades_is_active(self):
        r = evaluate_pair_status(_active(
            instrument=_robust_instrument(),
            live_trades=10, live_wins=5, live_losses=5,
        ))
        assert r.status == PairStatus.ACTIVE

    def test_metrics_snapshot_populated(self):
        r = evaluate_pair_status(_active(instrument="EUR_USD"))
        assert r.metrics["robustness_combined_pf"] == ROBUSTNESS_COMBINED_PF["EUR_USD"]
        assert r.metrics["modeled_spread"] == MODELED_SPREAD["EUR_USD"]
        assert r.metrics["live_trades"] == 0


# ── watchlist: fragile robustness alone ──────────────────────────────────────

class TestWatchlistFragileRobustness:
    def test_fragile_pair_goes_to_watchlist(self):
        inst = _fragile_instrument()
        r = evaluate_pair_status(_active(instrument=inst))
        assert r.status == PairStatus.WATCHLIST
        assert "FRAGILE_ROBUSTNESS" in r.reasons

    def test_robust_pair_not_watchlisted(self):
        inst = _robust_instrument()
        r = evaluate_pair_status(_active(instrument=inst))
        assert r.status == PairStatus.ACTIVE
        assert "FRAGILE_ROBUSTNESS" not in r.reasons

    def test_fragile_pair_still_executes_trades(self):
        # watchlist does NOT disable — pair still runs
        inst = _fragile_instrument()
        r = evaluate_pair_status(_active(instrument=inst, live_trades=5, live_wins=2))
        assert r.status == PairStatus.WATCHLIST  # not DISABLED


# ── watchlist: spread breach alone ───────────────────────────────────────────

class TestWatchlistSpreadBreach:
    def test_high_spread_with_enough_obs_is_watchlist(self):
        inst = _robust_instrument()
        modeled = MODELED_SPREAD[inst]
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            days_since_live=30,
            realized_spread_mean=modeled * (SPREAD_MULTIPLE_THRESHOLD + 0.1),
            spread_obs_count=SPREAD_OBS_MINIMUM,
        ))
        assert r.status == PairStatus.WATCHLIST
        assert "SPREAD_BREACH" in r.reasons

    def test_high_spread_below_obs_minimum_not_flagged(self):
        inst = _robust_instrument()
        modeled = MODELED_SPREAD[inst]
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            days_since_live=30,
            realized_spread_mean=modeled * (SPREAD_MULTIPLE_THRESHOLD + 1.0),
            spread_obs_count=SPREAD_OBS_MINIMUM - 1,
        ))
        assert r.status == PairStatus.ACTIVE

    def test_spread_within_threshold_not_flagged(self):
        inst = _robust_instrument()
        modeled = MODELED_SPREAD[inst]
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            days_since_live=30,
            realized_spread_mean=modeled * 1.5,  # below 2x threshold
            spread_obs_count=SPREAD_OBS_MINIMUM,
        ))
        assert r.status == PairStatus.ACTIVE


# ── watchlist: zero entries after 60 days ────────────────────────────────────

class TestWatchlistZeroEntries:
    def test_zero_entries_after_60_days_is_watchlist(self):
        inst = _robust_instrument()
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            live_trades=0,
            days_since_live=ZERO_ENTRY_DAYS_THRESHOLD,
        ))
        assert r.status == PairStatus.WATCHLIST
        assert any("ZERO_ENTRIES" in reason for reason in r.reasons)

    def test_zero_entries_before_60_days_is_active(self):
        inst = _robust_instrument()
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            live_trades=0,
            days_since_live=ZERO_ENTRY_DAYS_THRESHOLD - 1,
        ))
        assert r.status == PairStatus.ACTIVE

    def test_has_trades_not_flagged_regardless_of_days(self):
        inst = _robust_instrument()
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            live_trades=1,
            live_wins=1,
            days_since_live=100,
        ))
        assert r.status == PairStatus.ACTIVE


# ── disable: 15+ losses with 0 wins ──────────────────────────────────────────

class TestDisableZeroWins:
    def test_15_losses_zero_wins_disabled(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=ZERO_WIN_LOSS_THRESHOLD,
            live_wins=0,
            live_losses=ZERO_WIN_LOSS_THRESHOLD,
            days_since_live=30,
        ))
        assert r.status == PairStatus.DISABLED
        assert any("ZERO_WINS" in reason for reason in r.reasons)

    def test_14_losses_zero_wins_not_yet_disabled(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=ZERO_WIN_LOSS_THRESHOLD - 1,
            live_wins=0,
            live_losses=ZERO_WIN_LOSS_THRESHOLD - 1,
            days_since_live=30,
        ))
        assert r.status != PairStatus.DISABLED

    def test_15_losses_one_win_not_disabled(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=ZERO_WIN_LOSS_THRESHOLD + 1,
            live_wins=1,
            live_losses=ZERO_WIN_LOSS_THRESHOLD,
            days_since_live=30,
        ))
        assert r.status != PairStatus.DISABLED

    def test_reason_includes_loss_count(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=20,
            live_wins=0,
            live_losses=20,
            days_since_live=30,
        ))
        assert r.status == PairStatus.DISABLED
        assert any("20" in reason for reason in r.reasons)


# ── disable: loss concentration + 0 wins ─────────────────────────────────────

class TestDisableLossConcentration:
    def test_80pct_losses_zero_wins_disabled(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=5,
            live_wins=0,
            live_losses=5,
            pair_gross_loss=800.0,
            portfolio_gross_loss=1000.0,  # 80%
            days_since_live=30,
        ))
        assert r.status == PairStatus.DISABLED
        assert any("LOSS_CONCENTRATION" in reason for reason in r.reasons)

    def test_79pct_losses_not_disabled(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=5,
            live_wins=0,
            live_losses=5,
            pair_gross_loss=790.0,
            portfolio_gross_loss=1000.0,  # 79%
            days_since_live=30,
        ))
        # May be watchlist but not disabled by this rule
        assert "LOSS_CONCENTRATION" not in " ".join(r.reasons)

    def test_high_concentration_with_one_win_not_disabled_by_this_rule(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=6,
            live_wins=1,
            live_losses=5,
            pair_gross_loss=800.0,
            portfolio_gross_loss=1000.0,
            days_since_live=30,
        ))
        assert "LOSS_CONCENTRATION" not in " ".join(r.reasons)


# ── disable: fragile robustness AND spread breach ────────────────────────────

class TestDisableCombinedRobustnessSpread:
    def test_fragile_plus_spread_breach_disables(self):
        inst = _fragile_instrument()
        modeled = MODELED_SPREAD[inst]
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            days_since_live=30,
            realized_spread_mean=modeled * (SPREAD_MULTIPLE_THRESHOLD + 0.5),
            spread_obs_count=SPREAD_OBS_MINIMUM,
        ))
        assert r.status == PairStatus.DISABLED
        assert "FRAGILE_ROBUSTNESS_AND_SPREAD_BREACH" in r.reasons

    def test_fragile_without_spread_breach_is_only_watchlist(self):
        inst = _fragile_instrument()
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            days_since_live=30,
            realized_spread_mean=None,
            spread_obs_count=0,
        ))
        assert r.status == PairStatus.WATCHLIST
        assert "FRAGILE_ROBUSTNESS" in r.reasons

    def test_spread_breach_without_fragile_is_only_watchlist(self):
        inst = _robust_instrument()
        modeled = MODELED_SPREAD[inst]
        r = evaluate_pair_status(PairMetrics(
            instrument=inst,
            days_since_live=30,
            realized_spread_mean=modeled * (SPREAD_MULTIPLE_THRESHOLD + 0.5),
            spread_obs_count=SPREAD_OBS_MINIMUM,
        ))
        assert r.status == PairStatus.WATCHLIST
        assert "SPREAD_BREACH" in r.reasons


# ── separation: overall LCR status vs pair status ────────────────────────────

class TestStrategyVsPairSeparation:
    """
    Pair-level status is independent of overall LCR strategy status.
    A pair can be disabled while LCR overall is still 'unknown' (unproven).
    These tests verify the pure function has no knowledge of overall LCR status
    and that the function returns structured reasons regardless of strategy state.
    """

    def test_disabled_pair_has_machine_readable_reasons(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=20,
            live_wins=0,
            live_losses=20,
            days_since_live=30,
        ))
        assert r.status == PairStatus.DISABLED
        assert isinstance(r.reasons, list)
        assert len(r.reasons) > 0
        assert all(isinstance(reason, str) for reason in r.reasons)

    def test_disabled_pair_has_metrics_dict(self):
        r = evaluate_pair_status(PairMetrics(
            instrument="EUR_USD",
            live_trades=20,
            live_wins=0,
            live_losses=20,
            days_since_live=30,
        ))
        assert isinstance(r.metrics, dict)
        assert "live_trades" in r.metrics
        assert "robustness_combined_pf" in r.metrics

    def test_pairs_evaluated_independently(self):
        """Each pair's status depends only on its own metrics, not other pairs."""
        r_eur = evaluate_pair_status(_active(instrument="EUR_USD"))
        r_usdcad = evaluate_pair_status(_active(instrument="USD_CAD"))
        # EUR_USD should be active (robust), USD_CAD should be watchlist (fragile)
        assert r_eur.status == PairStatus.ACTIVE
        assert r_usdcad.status == PairStatus.WATCHLIST

    def test_unknown_instrument_has_no_robustness_pf(self):
        r = evaluate_pair_status(PairMetrics(instrument="GBP_CHF", days_since_live=5))
        assert r.metrics["robustness_combined_pf"] is None
        assert r.metrics["modeled_spread"] is None
        # No fragile flag for unknown pair
        assert r.status == PairStatus.ACTIVE


# ── context output includes pair statuses ────────────────────────────────────

class TestContextOutput:
    """Verify that evaluate_pair_status returns all fields required for audit and display."""

    def test_all_required_fields_present(self):
        r = evaluate_pair_status(_active(instrument="NZD_USD"))
        assert hasattr(r, "instrument")
        assert hasattr(r, "status")
        assert hasattr(r, "reasons")
        assert hasattr(r, "metrics")

    def test_metrics_contains_all_keys(self):
        r = evaluate_pair_status(_active(instrument="AUD_USD"))
        required = {
            "live_trades", "live_wins", "live_losses",
            "days_since_live", "robustness_combined_pf",
            "modeled_spread", "realized_spread_mean", "spread_obs_count",
        }
        assert required.issubset(r.metrics.keys())

    def test_multiple_pairs_evaluate_correctly(self):
        pairs = ["EUR_USD", "NZD_USD", "AUD_USD", "EUR_JPY", "USD_CAD"]
        results = [evaluate_pair_status(_active(instrument=p)) for p in pairs]
        robust = [r for r in results if r.metrics["robustness_combined_pf"] is not None and r.metrics["robustness_combined_pf"] >= 1.0]
        fragile = [r for r in results if r.metrics["robustness_combined_pf"] is not None and r.metrics["robustness_combined_pf"] < 1.0]
        assert all(r.status == PairStatus.ACTIVE for r in robust)
        assert all(r.status == PairStatus.WATCHLIST for r in fragile)
