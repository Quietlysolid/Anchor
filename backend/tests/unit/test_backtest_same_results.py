"""
Regression test: backtest must produce different results for different instruments.

Bug reported: the Historical Test page shows identical metrics for every currency pair,
which indicates either:
  (a) all backtests silently produce 0 trades (all-zero results), OR
  (b) some shared state leaks between runs causing identical non-zero results.

Root-cause hypothesis: asyncio.run() inside run_in_executor thread raises
RuntimeError or the signal pipeline suppresses every bar, so compute_results()
returns the same all-zero BacktestResults for every instrument.

This test:
  1. Patches ConfluenceEngine.evaluate to return a deterministic LONG signal for
     every bar that passes the session filter — removing dependency on real signal
     logic so the test focuses purely on the backtest infrastructure.
  2. Runs BacktestEngine for EUR_USD (price ≈1.10) and GBP_USD (price ≈1.30).
  3. Asserts that BOTH produce trades (> 0) and that P&L results DIFFER between
     the two instruments (because different price levels → different ATR → different
     dollar P&L, even with identical win/loss rates).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from anchor.backtesting.engine import BacktestEngine
from anchor.signals.engine import SignalResult


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_london_h1_bars(
    start: datetime,
    n_days: int,
    base_price: float,
    atr_pips: float = 0.0010,
) -> pd.DataFrame:
    """
    Generate synthetic H1 OHLCV candles covering London session hours (08:00–12:00 UTC)
    for n_days weekdays starting from `start`.

    Price trends up slightly every bar so ATR is non-degenerate.
    All timestamps are timezone-aware UTC.
    """
    rows = []
    price = base_price
    day = start
    days_generated = 0
    while days_generated < n_days:
        if day.weekday() < 5:  # Mon–Fri only
            for hour in range(8, 13):  # 08:00–12:00 UTC (London session)
                ts = day.replace(hour=hour, minute=0, second=0, microsecond=0)
                price += atr_pips * 0.1  # gentle uptrend
                rows.append({
                    "time": ts,
                    "open":   price,
                    "high":   price + atr_pips,
                    "low":    price - atr_pips * 0.5,
                    "close":  price + atr_pips * 0.3,
                    "volume": 1000.0,
                })
            days_generated += 1
        day += timedelta(days=1)
    return pd.DataFrame(rows)


def _always_long_signal(instrument: str) -> SignalResult:
    """Return a LONG signal with a high confluence score, never suppressed."""
    return SignalResult(
        instrument=instrument,
        direction="LONG",
        confluence_score=0.85,
        rsi_score=0.8,
        bb_kc_score=0.9,
        adx_score=0.8,
        sr_score=0.7,
        mtf_score=0.8,
        csi_score=0.5,
        ml_confidence=None,
        regime_state="TRENDING",
        session="LONDON",
        suppressed=False,
        suppression_reason=None,
    )


# ── fixtures ─────────────────────────────────────────────────────────────────

START = datetime(2023, 1, 2, tzinfo=timezone.utc)  # first Monday of 2023
N_DAYS = 90  # ~3 months of data — enough to generate many trades


@pytest.fixture()
def eur_usd_df() -> pd.DataFrame:
    return _make_london_h1_bars(START, N_DAYS, base_price=1.10)


@pytest.fixture()
def gbp_usd_df() -> pd.DataFrame:
    return _make_london_h1_bars(START, N_DAYS, base_price=1.30)


# ── tests ─────────────────────────────────────────────────────────────────────

class TestBacktestProducesTrades:
    """Guard: verify that the backtest infrastructure generates trades at all."""

    def test_produces_nonzero_trades_eur_usd(self, eur_usd_df):
        """With a signal that always fires, EUR/USD backtest must produce trades."""
        eng = BacktestEngine(initial_balance=10_000.0)
        eng.load_df("EUR_USD", "H1", eur_usd_df)

        with patch(
            "anchor.backtesting.engine.ConfluenceEngine.evaluate",
            new_callable=AsyncMock,
            side_effect=lambda instrument, dt: _always_long_signal(instrument),
        ):
            results = eng.run("EUR_USD", "H1")

        assert results.total_trades > 0, (
            f"Expected trades but got 0 — likely asyncio.run() is failing silently "
            f"inside the backtest loop or session filter is blocking all bars. "
            f"instrument={results.instrument}"
        )

    def test_produces_nonzero_trades_gbp_usd(self, gbp_usd_df):
        """With a signal that always fires, GBP/USD backtest must produce trades."""
        eng = BacktestEngine(initial_balance=10_000.0)
        eng.load_df("GBP_USD", "H1", gbp_usd_df)

        with patch(
            "anchor.backtesting.engine.ConfluenceEngine.evaluate",
            new_callable=AsyncMock,
            side_effect=lambda instrument, dt: _always_long_signal(instrument),
        ):
            results = eng.run("GBP_USD", "H1")

        assert results.total_trades > 0, (
            f"Expected trades but got 0 for GBP/USD. "
            f"instrument={results.instrument}"
        )


class TestBacktestDiffersAcrossPairs:
    """
    Core regression: results for EUR/USD and GBP/USD must NOT be identical.

    EUR/USD trades at price ≈1.10, GBP/USD at ≈1.30.  Same ATR-pips → different
    absolute ATR → different SL distances → different position sizes → different
    dollar P&L.  If both return the same net_pnl, final_balance, and total_trades,
    it means some shared state or silent failure is collapsing all results to the
    same value (the reported bug).
    """

    def _run(self, instrument: str, df: pd.DataFrame) -> object:
        eng = BacktestEngine(initial_balance=10_000.0)
        eng.load_df(instrument, "H1", df)
        with patch(
            "anchor.backtesting.engine.ConfluenceEngine.evaluate",
            new_callable=AsyncMock,
            side_effect=lambda instrument, dt: _always_long_signal(instrument),
        ):
            return eng.run(instrument, "H1")

    def test_different_instruments_produce_different_net_pnl(
        self, eur_usd_df, gbp_usd_df
    ):
        eur = self._run("EUR_USD", eur_usd_df)
        gbp = self._run("GBP_USD", gbp_usd_df)

        # Both must have trades — if either is 0 it indicates a different bug
        assert eur.total_trades > 0, "EUR/USD produced no trades — asyncio or session bug"
        assert gbp.total_trades > 0, "GBP/USD produced no trades — asyncio or session bug"

        # The core assertion: results must differ
        assert eur.net_pnl != gbp.net_pnl, (
            f"BUG REPRODUCED: EUR/USD and GBP/USD returned identical net_pnl={eur.net_pnl}. "
            f"All pairs are producing the same result."
        )

    def test_different_instruments_produce_different_final_balance(
        self, eur_usd_df, gbp_usd_df
    ):
        eur = self._run("EUR_USD", eur_usd_df)
        gbp = self._run("GBP_USD", gbp_usd_df)

        assert eur.final_balance != gbp.final_balance, (
            f"BUG REPRODUCED: final_balance identical across pairs "
            f"({eur.final_balance}). Shared state or all-zero results suspected."
        )

    def test_instrument_field_matches_requested_pair(self, eur_usd_df, gbp_usd_df):
        """Sanity: the returned instrument name must match what was requested."""
        eur = self._run("EUR_USD", eur_usd_df)
        gbp = self._run("GBP_USD", gbp_usd_df)

        assert eur.instrument == "EUR_USD", f"instrument field wrong: {eur.instrument}"
        assert gbp.instrument == "GBP_USD", f"instrument field wrong: {gbp.instrument}"


class TestRunInExecutor:
    """
    Verify that BacktestEngine.run() works correctly when called from a thread
    executor, which is how the API invokes it (run_in_executor).

    The engine reuses a single event loop (new_event_loop + run_until_complete)
    for all bar evaluations — creating a new loop per bar (asyncio.run()) was
    50–100x slower and caused 504 timeouts on multi-year datasets.
    """

    def test_run_in_executor_produces_trades(self, eur_usd_df):
        """BacktestEngine via run_in_executor must produce trades."""
        import concurrent.futures

        eng = BacktestEngine(initial_balance=10_000.0)
        eng.load_df("EUR_USD", "H1", eur_usd_df)

        def _run():
            with patch(
                "anchor.backtesting.engine.ConfluenceEngine.evaluate",
                new_callable=AsyncMock,
                side_effect=lambda instrument, dt: _always_long_signal(instrument),
            ):
                return eng.run("EUR_USD", "H1")

        outer_loop = asyncio.new_event_loop()
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = outer_loop.run_in_executor(pool, _run)
                results = outer_loop.run_until_complete(future)
        finally:
            outer_loop.close()

        assert results.total_trades > 0, (
            "BacktestEngine inside run_in_executor produced 0 trades — "
            "the single-loop run_until_complete approach is broken."
        )


class TestSignalErrorsNotSilentlySwallowed:
    """
    The backtest loop catches EVERY exception from evaluate() silently and logs
    a warning.  If evaluate() throws on every bar (e.g. NewsFilter holding a
    stale event-loop reference after asyncio.run() tears down the loop), 0 trades
    are generated and all pairs look identical.

    This test ensures that when evaluate() is working correctly, the warning
    logger is NOT called.  If signal_error warnings fire for every London bar,
    the test exposes that the exception-swallowing is the proximate cause.
    """

    def test_no_signal_errors_when_evaluate_works(self, eur_usd_df):
        """evaluate() must NOT raise on any bar when the signal mock is correct."""
        eng = BacktestEngine(initial_balance=10_000.0)
        eng.load_df("EUR_USD", "H1", eur_usd_df)

        warned_bars: list[str] = []

        original_warning = eng.run.__func__  # not needed; capture via logger patch

        with patch(
            "anchor.backtesting.engine.ConfluenceEngine.evaluate",
            new_callable=AsyncMock,
            side_effect=lambda instrument, dt: _always_long_signal(instrument),
        ), patch(
            "anchor.backtesting.engine.logger.warning",
        ) as mock_warn:
            eng.run("EUR_USD", "H1")
            warned_bars = [call.kwargs.get("bar", "") for call in mock_warn.call_args_list]

        assert len(warned_bars) == 0, (
            f"signal_error logged for {len(warned_bars)} bars — evaluate() is "
            f"throwing silently on every bar.  First few: {warned_bars[:5]}.  "
            f"This is the root cause of all pairs showing identical (zero) results."
        )


class TestRealEvaluatePipeline:
    """
    Integration test: the REAL ConfluenceEngine.evaluate() must complete without
    throwing exceptions when called with properly-indexed DataFrames.

    Root-cause regression for the _set_index bug:
      When the backtest engine passes DataFrames with an integer RangeIndex
      (i.e. 'time' left as a column instead of being set as the index),
      _tsmom_direction() tries to subtract pd.Timedelta from an integer:
          cutoff = df_daily.index[-1] - pd.Timedelta(days=lookback_days)
      This raises TypeError, which is caught silently by the per-bar
      try/except block in BacktestEngine.run(), producing 0 trades on every bar.

    The fix: engine.py calls _set_index(window) before update_cache() so that
    the DataFrames stored in data_cache always have a DatetimeIndex.

    This test exercises the REAL evaluate() (not mocked) using:
      - synthetic H1/H4/D DataFrames with DatetimeIndex (correct, post-fix)
      - ablation_session=False so bars outside London hours still go through
        the full signal pipeline (steps 3–7) rather than being session-filtered
      - ablation of Redis-dependent macro signals so the test is hermetic
        (no Redis needed)

    Assertions:
      1. No exceptions are thrown on any evaluated bar.
      2. At least some bars reach the scoring step (not all blocked early).
    """

    def _make_indexed_bars(
        self,
        start: datetime,
        n: int,
        base_price: float,
        tf_hours: int,
        atr: float,
    ) -> pd.DataFrame:
        """
        Generate synthetic OHLCV bars with 'time' as the DataFrame INDEX
        (DatetimeIndex, UTC-aware).  This is what _set_index() produces and
        what the signal functions expect.
        """
        from datetime import timedelta

        rows = []
        price = base_price
        t = start
        for _ in range(n):
            # skip weekends
            while t.weekday() >= 5:
                t += timedelta(hours=tf_hours)
            price += atr * 0.1
            rows.append({
                "time": t,
                "open":   price,
                "high":   price + atr,
                "low":    price - atr * 0.5,
                "close":  price + atr * 0.3,
                "volume": 1000.0,
            })
            t += timedelta(hours=tf_hours)

        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df.set_index("time")

    def test_real_evaluate_does_not_throw(self):
        """
        The real evaluate() must complete on every bar without raising.

        This directly guards against the _set_index regression: if 'time' were
        left as a column (integer RangeIndex), _tsmom_direction would raise
            TypeError: unsupported operand type(s) for -: 'int' and 'Timedelta'
        which the backtest engine silently swallows, causing 0 trades.
        """
        from anchor.signals.engine import ConfluenceEngine

        START = datetime(2023, 1, 2, 9, 0, tzinfo=timezone.utc)

        h1_df = self._make_indexed_bars(START, 300, 1.10, 1,  0.001)
        h4_df = self._make_indexed_bars(START, 200, 1.10, 4,  0.004)
        d1_df = self._make_indexed_bars(START, 200, 1.10, 24, 0.010)

        data_cache = {
            "H1": {"EUR_USD": h1_df},
            "H4": {"EUR_USD": h4_df},
            "D":  {"EUR_USD": d1_df},
        }
        engine = ConfluenceEngine(
            data_cache=data_cache,
            # Disable session filter so ALL hours go through the full pipeline
            ablation_session=False,
            # Disable Redis-dependent macro signals — no Redis in test environment
            ablation_sentiment=False,
            ablation_rate_divergence=False,
            ablation_order_book=False,
            ablation_cme_flow=False,
            ablation_fx_options=False,
            ablation_econ_surprise=False,
            ablation_cross_asset=False,
        )

        exceptions_raised: list[str] = []
        completions = 0
        n_bars = 20

        for i in range(n_bars):
            bar_time = START + timedelta(hours=i + 100)
            try:
                asyncio.run(engine.evaluate("EUR_USD", bar_time))
                completions += 1
            except Exception as exc:
                exceptions_raised.append(f"bar {i}: {type(exc).__name__}: {exc}")

        assert len(exceptions_raised) == 0, (
            f"Real evaluate() threw exceptions on {len(exceptions_raised)}/{n_bars} bars.  "
            f"This indicates the _set_index fix is missing or broken.  "
            f"First exception: {exceptions_raised[0] if exceptions_raised else 'none'}.  "
            f"Root cause: DataFrames in data_cache have integer index instead of "
            f"DatetimeIndex, causing TypeError in _tsmom_direction()."
        )
        assert completions == n_bars, (
            f"Only {completions}/{n_bars} bars completed — some bars raised."
        )

    def test_real_evaluate_without_set_index_raises(self):
        """
        Regression guard: confirm that the bug EXISTS when DataFrames are NOT
        indexed by time.  This test proves the necessity of the _set_index fix
        by showing that an integer-indexed DataFrame causes TypeError in evaluate().

        If this test ever starts PASSING (i.e. no exception raised), it means the
        signal pipeline was refactored to handle integer-indexed DataFrames — which
        would make the _set_index call in engine.py redundant but not harmful.
        """
        from datetime import timedelta
        from anchor.signals.engine import ConfluenceEngine

        START = datetime(2023, 1, 2, 9, 0, tzinfo=timezone.utc)

        def _make_unindexed_bars(start, n, base, tf_hours, atr):
            rows = []
            price = base
            t = start
            for _ in range(n):
                while t.weekday() >= 5:
                    t += timedelta(hours=tf_hours)
                price += atr * 0.1
                rows.append({
                    "time": t, "open": price, "high": price + atr,
                    "low": price - atr * 0.5, "close": price + atr * 0.3, "volume": 1000.0,
                })
                t += timedelta(hours=tf_hours)
            df = pd.DataFrame(rows)
            df["time"] = pd.to_datetime(df["time"], utc=True)
            return df  # time stays as a COLUMN — integer RangeIndex

        h1_df = _make_unindexed_bars(START, 300, 1.10, 1,  0.001)
        h4_df = _make_unindexed_bars(START, 200, 1.10, 4,  0.004)
        d1_df = _make_unindexed_bars(START, 200, 1.10, 24, 0.010)

        data_cache = {
            "H1": {"EUR_USD": h1_df},
            "H4": {"EUR_USD": h4_df},
            "D":  {"EUR_USD": d1_df},
        }
        engine = ConfluenceEngine(
            data_cache=data_cache,
            ablation_session=False,
            ablation_sentiment=False,
            ablation_rate_divergence=False,
            ablation_order_book=False,
            ablation_cme_flow=False,
            ablation_fx_options=False,
            ablation_econ_surprise=False,
            ablation_cross_asset=False,
        )

        bar_time = START + timedelta(hours=105)
        raised = False
        try:
            asyncio.run(engine.evaluate("EUR_USD", bar_time))
        except (TypeError, AttributeError):
            raised = True

        assert raised, (
            "Expected TypeError/AttributeError when data_cache has integer-indexed "
            "DataFrames (time as column), but no exception was raised.  This means "
            "the signal pipeline was updated to handle integer-indexed DataFrames — "
            "verify that _set_index() in engine.py is still needed."
        )
