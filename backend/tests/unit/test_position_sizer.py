"""
Unit tests for PositionSizer.

Validates pip value logic, fixed-fractional sizing, and hard caps
across all supported instrument types.
"""
import pytest
from anchor.risk.position_sizer import PositionSizer, MICRO_LOT, set_usdjpy_rate

SIZER = PositionSizer()
BALANCE = 200.0  # Starting capital


class TestUsdQuotedPairs:
    """EUR_USD, GBP_USD, AUD_USD, NZD_USD — pip value is 0.0001 USD/unit."""

    def test_eurusd_basic_sizing(self):
        # 1% of $200 = $2 risk, 20-pip SL (0.0020), pip_val=0.0001
        # units = 2 / (20 * 0.0001) = 2 / 0.002 = 1000
        units = SIZER.compute(
            account_balance=BALANCE,
            instrument="EUR_USD",
            entry_price=1.0800,
            stop_loss=1.0780,  # 20 pips
        )
        assert units == MICRO_LOT  # 1,000 units

    def test_eurusd_wider_stop_smaller_size(self):
        # 50-pip SL → units = 2 / (50 * 0.0001) = 400 → snapped to 0 → floor to MICRO_LOT
        units = SIZER.compute(
            account_balance=BALANCE,
            instrument="EUR_USD",
            entry_price=1.0800,
            stop_loss=1.0750,  # 50 pips
        )
        assert units == MICRO_LOT  # Always minimum 1 micro lot

    def test_larger_account_scales_up(self):
        units = SIZER.compute(
            account_balance=10_000.0,
            instrument="EUR_USD",
            entry_price=1.0800,
            stop_loss=1.0750,  # 50 pips
        )
        # risk = 100, stop_pips=50, pip_val=0.0001 → 100/(50*0.0001)=20,000
        assert units == 20_000

    def test_result_is_multiple_of_micro_lot(self):
        units = SIZER.compute(
            account_balance=5_000.0,
            instrument="GBP_USD",
            entry_price=1.2700,
            stop_loss=1.2680,  # 20 pips
        )
        assert units % MICRO_LOT == 0


class TestUsdBasePairs:
    """USD_JPY, USD_CHF, USD_CAD — pip value = pip_size / entry_price."""

    def test_usdjpy_sizing(self):
        # pip_val = 0.01 / 150.0 = 0.0000667
        # risk = 2, stop_pips = 20 → units = 2 / (20 * 0.0000667) ≈ 1,500 → snapped to 1,000
        units = SIZER.compute(
            account_balance=BALANCE,
            instrument="USD_JPY",
            entry_price=150.00,
            stop_loss=149.80,  # 20 pips
        )
        assert units >= MICRO_LOT
        assert units % MICRO_LOT == 0

    def test_usdjpy_higher_rate_smaller_size(self):
        # Higher entry = smaller pip value = more units (inverse)
        units_low = SIZER.compute(
            account_balance=10_000.0,
            instrument="USD_JPY",
            entry_price=100.00,
            stop_loss=99.80,
        )
        units_high = SIZER.compute(
            account_balance=10_000.0,
            instrument="USD_JPY",
            entry_price=150.00,
            stop_loss=149.80,
        )
        # Higher price → lower pip_value_per_unit → more units
        assert units_high > units_low


class TestJpyCrossPairs:
    """EUR_JPY, GBP_JPY — pip value uses USD/JPY approximation."""

    def test_eurjpy_sizing(self):
        set_usdjpy_rate(150.0)
        units = SIZER.compute(
            account_balance=10_000.0,
            instrument="EUR_JPY",
            entry_price=160.00,
            stop_loss=159.80,  # 20 pips
        )
        # pip_val = 0.01 / 150 ≈ 0.0000667
        # risk = 100, stop_pips=20 → units = 100 / (20 * 0.0000667) ≈ 75,000
        assert units >= MICRO_LOT
        assert units % MICRO_LOT == 0

    def test_usdjpy_rate_update(self):
        set_usdjpy_rate(120.0)
        units_120 = SIZER.compute(
            account_balance=10_000.0,
            instrument="EUR_JPY",
            entry_price=160.00,
            stop_loss=159.80,
        )
        set_usdjpy_rate(160.0)
        units_160 = SIZER.compute(
            account_balance=10_000.0,
            instrument="EUR_JPY",
            entry_price=160.00,
            stop_loss=159.80,
        )
        # Higher USD/JPY → higher pip value → fewer units
        assert units_120 > units_160


class TestEdgeCases:
    def test_zero_stop_distance_returns_minimum(self):
        units = SIZER.compute(
            account_balance=BALANCE,
            instrument="EUR_USD",
            entry_price=1.0800,
            stop_loss=1.0800,  # identical — zero distance
        )
        assert units == MICRO_LOT

    def test_kelly_fraction_reduces_size(self):
        units_no_kelly = SIZER.compute(
            account_balance=10_000.0,
            instrument="EUR_USD",
            entry_price=1.0800,
            stop_loss=1.0780,
        )
        units_with_kelly = SIZER.compute(
            account_balance=10_000.0,
            instrument="EUR_USD",
            entry_price=1.0800,
            stop_loss=1.0780,
            kelly_fraction=0.5,
        )
        assert units_with_kelly <= units_no_kelly

    def test_drawdown_scale_halves_size(self):
        normal = SIZER.compute(
            account_balance=10_000.0,
            instrument="EUR_USD",
            entry_price=1.0800,
            stop_loss=1.0780,
            drawdown_scale=1.0,
        )
        reduced = SIZER.compute(
            account_balance=10_000.0,
            instrument="EUR_USD",
            entry_price=1.0800,
            stop_loss=1.0780,
            drawdown_scale=0.5,
        )
        assert reduced <= normal // 2 + MICRO_LOT  # roughly half, accounting for rounding

    def test_compute_units_convenience(self):
        units = SIZER.compute_units(
            account_balance=10_000.0,
            stop_distance=0.0020,  # 20 pips on EUR_USD
            instrument="EUR_USD",
            entry_price=1.0800,
        )
        assert units >= MICRO_LOT
        assert units % MICRO_LOT == 0
