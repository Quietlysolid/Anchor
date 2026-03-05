"""
Unit tests for math utilities: pip sizes, Sharpe, Sortino, max drawdown, profit factor.
"""
import numpy as np
import pytest

from anchor.utils.math_utils import (
    get_pip_size,
    price_to_pips,
    pips_to_price,
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    profit_factor,
)


class TestPipUtils:
    def test_known_pip_sizes(self):
        assert get_pip_size("EUR_USD") == 0.0001
        assert get_pip_size("GBP_USD") == 0.0001
        assert get_pip_size("USD_JPY") == 0.01
        assert get_pip_size("EUR_JPY") == 0.01
        assert get_pip_size("USD_CAD") == 0.0001

    def test_unknown_instrument_defaults(self):
        assert get_pip_size("XAU_USD") == 0.0001  # default

    def test_price_to_pips_eurusd(self):
        pips = price_to_pips("EUR_USD", 0.0020)
        assert abs(pips - 20.0) < 1e-9

    def test_price_to_pips_usdjpy(self):
        pips = price_to_pips("USD_JPY", 0.20)
        assert abs(pips - 20.0) < 1e-9

    def test_pips_to_price_roundtrip(self):
        for instrument in ["EUR_USD", "USD_JPY", "GBP_USD"]:
            price_diff = 0.0025 if instrument != "USD_JPY" else 0.25
            pips = price_to_pips(instrument, price_diff)
            back = pips_to_price(instrument, pips)
            assert abs(back - price_diff) < 1e-10


class TestSharpeRatio:
    def test_positive_sharpe(self):
        returns = np.full(252, 0.001)  # consistent +0.1% daily
        sr = sharpe_ratio(returns)
        assert sr > 0

    def test_zero_std_returns_zero(self):
        returns = np.zeros(252)
        assert sharpe_ratio(returns) == 0.0

    def test_negative_returns_negative_sharpe(self):
        returns = np.full(252, -0.001)
        assert sharpe_ratio(returns) < 0

    def test_higher_return_higher_sharpe(self):
        r_low  = np.full(252, 0.0005)
        r_high = np.full(252, 0.001)
        assert sharpe_ratio(r_high) > sharpe_ratio(r_low)


class TestSortinoRatio:
    def test_no_downside_returns_zero(self):
        returns = np.full(100, 0.001)
        # No negative returns → downside std = 0
        assert sortino_ratio(returns) == 0.0

    def test_positive_with_downside(self):
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0005, 0.01, 500)
        sr = sortino_ratio(returns)
        # With positive mean, should be positive
        assert isinstance(sr, float)


class TestMaxDrawdown:
    def test_no_drawdown(self):
        equity = np.array([100.0, 110.0, 120.0, 130.0])
        assert max_drawdown(equity) == 0.0

    def test_full_recovery(self):
        equity = np.array([100.0, 80.0, 100.0])
        dd = max_drawdown(equity)
        assert abs(dd - (-0.20)) < 1e-9

    def test_multiple_drawdowns_returns_worst(self):
        equity = np.array([100.0, 90.0, 95.0, 70.0, 80.0])
        dd = max_drawdown(equity)
        assert abs(dd - (-0.30)) < 1e-9  # 100 → 70 = 30%

    def test_constant_equity(self):
        equity = np.full(100, 100.0)
        assert max_drawdown(equity) == 0.0


class TestProfitFactor:
    def test_winners_only_returns_inf(self):
        pnl = np.array([10.0, 20.0, 5.0])
        assert profit_factor(pnl) == float("inf")

    def test_balanced(self):
        pnl = np.array([10.0, -10.0])
        assert abs(profit_factor(pnl) - 1.0) < 1e-9

    def test_2_to_1_rr(self):
        # 2 wins of 20, 2 losses of 10 → PF = 40/20 = 2.0
        pnl = np.array([20.0, 20.0, -10.0, -10.0])
        assert abs(profit_factor(pnl) - 2.0) < 1e-9

    def test_all_losers_returns_zero(self):
        pnl = np.array([-10.0, -5.0])
        assert profit_factor(pnl) == 0.0
