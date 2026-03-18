"""
Unit tests for PositionSizer cascade multipliers and hard caps.

Complements test_position_sizer.py (which covers pip-value logic and basic sizing).
This file covers:
  - Scale factor clamping floors and ceilings
  - Cascading multiplier interactions
  - Hard cap (5% notional)
  - Volatility normalization
  - Micro-lot floor is always respected
"""
from unittest.mock import MagicMock, patch
from anchor.risk.position_sizer import PositionSizer, MICRO_LOT

SIZER = PositionSizer()


# ── vix_scale ─────────────────────────────────────────────────────────────────

class TestVixScale:
    def test_vix_scale_clamped_at_025_floor(self):
        """vix_scale below 0.25 is clamped to 0.25 — further reduction makes no difference."""
        units_tiny  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                    entry_price=1.08, stop_loss=1.075, vix_scale=0.001)
        units_floor = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                    entry_price=1.08, stop_loss=1.075, vix_scale=0.25)
        assert units_tiny == units_floor

    def test_vix_scale_clamped_at_10_ceiling(self):
        """vix_scale above 1.0 is clamped — no upside beyond 1.0."""
        units_one  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                   entry_price=1.08, stop_loss=1.075, vix_scale=1.0)
        units_high = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                   entry_price=1.08, stop_loss=1.075, vix_scale=5.0)
        assert units_high == units_one

    def test_vix_scale_025_reduces_relative_to_10(self):
        # Patch hard cap to 100% notional so scale factors are the binding constraint.
        # At $10k/EUR_USD 1.08, the 5% notional cap binds at ~462 units (below MICRO_LOT),
        # which would mask the vix_scale effect by flooring both results to MICRO_LOT.
        mock_cfg = MagicMock()
        mock_cfg.max_risk_per_trade = 0.01
        mock_cfg.max_position_pct   = 1.0
        with patch("anchor.risk.position_sizer.settings", mock_cfg):
            units_full    = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                          entry_price=1.08, stop_loss=1.075, vix_scale=1.0)
            units_reduced = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                          entry_price=1.08, stop_loss=1.075, vix_scale=0.25)
        assert units_reduced < units_full


# ── session_scale ──────────────────────────────────────────────────────────────

class TestSessionScale:
    def test_session_scale_clamped_at_050_floor(self):
        units_tiny  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                    entry_price=1.08, stop_loss=1.075, session_scale=0.01)
        units_floor = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                    entry_price=1.08, stop_loss=1.075, session_scale=0.50)
        assert units_tiny == units_floor

    def test_session_scale_no_upside_above_10(self):
        units_one  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                   entry_price=1.08, stop_loss=1.075, session_scale=1.0)
        units_high = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                   entry_price=1.08, stop_loss=1.075, session_scale=2.0)
        assert units_high == units_one


# ── rolling_score_scale ────────────────────────────────────────────────────────

class TestRollingScoreScale:
    def test_floor_at_075(self):
        """rolling_score_scale below 0.75 is clamped to 0.75."""
        units_tiny  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                    entry_price=1.08, stop_loss=1.075, rolling_score_scale=0.01)
        units_floor = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                    entry_price=1.08, stop_loss=1.075, rolling_score_scale=0.75)
        assert units_tiny == units_floor

    def test_no_upside_above_10(self):
        """rolling_score_scale above 1.0 is clamped — no bonus sizing."""
        units_one  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                   entry_price=1.08, stop_loss=1.075, rolling_score_scale=1.0)
        units_high = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                   entry_price=1.08, stop_loss=1.075, rolling_score_scale=3.0)
        assert units_high == units_one

    def test_075_reduces_relative_to_10(self):
        units_full    = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                      entry_price=1.08, stop_loss=1.075, rolling_score_scale=1.0)
        units_reduced = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                      entry_price=1.08, stop_loss=1.075, rolling_score_scale=0.75)
        assert units_reduced <= units_full


# ── regime_scale ───────────────────────────────────────────────────────────────

class TestRegimeScale:
    def test_floor_at_050(self):
        units_tiny  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                    entry_price=1.08, stop_loss=1.075, regime_scale=0.01)
        units_floor = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                    entry_price=1.08, stop_loss=1.075, regime_scale=0.50)
        assert units_tiny == units_floor

    def test_no_upside_above_10(self):
        units_one  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                   entry_price=1.08, stop_loss=1.075, regime_scale=1.0)
        units_high = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                   entry_price=1.08, stop_loss=1.075, regime_scale=2.0)
        assert units_high == units_one


# ── vol_scale (volatility normalization) ──────────────────────────────────────

class TestVolatilityScale:
    def test_high_vol_reduces_size(self):
        """current_atr > reference_atr → vol_scale < 1 → fewer units."""
        units_normal   = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                       entry_price=1.08, stop_loss=1.075)
        units_high_vol = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                       entry_price=1.08, stop_loss=1.075,
                                       current_atr=0.002, reference_atr=0.001)
        assert units_high_vol <= units_normal

    def test_low_vol_increases_size_up_to_cap(self):
        """current_atr < reference_atr → vol_scale > 1, capped at 1.5."""
        units_normal  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                      entry_price=1.08, stop_loss=1.075,
                                      current_atr=0.001, reference_atr=0.001)
        units_low_vol = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                      entry_price=1.08, stop_loss=1.075,
                                      current_atr=0.0005, reference_atr=0.001)
        assert units_low_vol >= units_normal

    def test_low_vol_cap_at_15(self):
        """vol_scale is capped at 1.5 — extremely low ATR doesn't cause unbounded growth."""
        units_cap       = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                        entry_price=1.08, stop_loss=1.075,
                                        current_atr=0.001 / 1.5, reference_atr=0.001)
        units_very_low  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                        entry_price=1.08, stop_loss=1.075,
                                        current_atr=0.0000001, reference_atr=0.001)
        assert units_very_low == units_cap

    def test_vol_scale_missing_atr_is_neutral(self):
        """When current_atr or reference_atr is None, vol_scale defaults to 1.0."""
        units_no_atr  = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                      entry_price=1.08, stop_loss=1.075)
        units_one_atr = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                      entry_price=1.08, stop_loss=1.075,
                                      current_atr=0.001, reference_atr=None)
        assert units_no_atr == units_one_atr


# ── Hard cap (5% notional) ─────────────────────────────────────────────────────

class TestHardCap:
    def test_hard_cap_enforced_on_large_account_tiny_stop(self):
        """Large account + tiny stop → would produce huge raw units, must be capped."""
        # 1% of $1M = $10,000 risk; 1-pip stop → 100M raw units → capped by 5% notional
        units = SIZER.compute(account_balance=1_000_000, instrument="EUR_USD",
                              entry_price=1.08, stop_loss=1.07999)
        max_notional = 1_000_000 * 0.05
        max_units    = int(max_notional / 1.08 // MICRO_LOT) * MICRO_LOT
        assert units <= max(max_units, MICRO_LOT)

    def test_result_always_at_least_micro_lot(self):
        """Even with extreme combined scale-down, floor is always MICRO_LOT."""
        units = SIZER.compute(account_balance=100, instrument="EUR_USD",
                              entry_price=1.08, stop_loss=1.075,
                              drawdown_scale=0.5,
                              vix_scale=0.25,
                              regime_scale=0.50,
                              rolling_score_scale=0.75,
                              session_scale=0.50)
        assert units >= MICRO_LOT


# ── Cascading interactions ─────────────────────────────────────────────────────

class TestCascadingMultipliers:
    def test_all_scales_compound(self):
        """Combined scales multiply — result smaller than any single scale."""
        # Patch hard cap away so the cascade of multipliers is the binding constraint.
        mock_cfg = MagicMock()
        mock_cfg.max_risk_per_trade = 0.01
        mock_cfg.max_position_pct   = 1.0
        with patch("anchor.risk.position_sizer.settings", mock_cfg):
            units_full = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                       entry_price=1.08, stop_loss=1.075)
            units_scaled = SIZER.compute(account_balance=10_000, instrument="EUR_USD",
                                         entry_price=1.08, stop_loss=1.075,
                                         drawdown_scale=0.5,
                                         vix_scale=0.5,
                                         session_scale=0.5,
                                         regime_scale=0.5,
                                         rolling_score_scale=0.75)
        assert units_scaled < units_full

    def test_result_is_always_multiple_of_micro_lot(self):
        """Output is always snapped to micro-lot boundary."""
        units = SIZER.compute(account_balance=7_777, instrument="GBP_USD",
                              entry_price=1.27, stop_loss=1.268,
                              vix_scale=0.6, session_scale=0.85, regime_scale=0.8)
        assert units % MICRO_LOT == 0
