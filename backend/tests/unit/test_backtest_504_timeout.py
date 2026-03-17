"""
Test that reproduces the 504 Gateway Timeout bug on the backtest API endpoint.

Root cause: POST /backtest/run runs a full multi-year backtest synchronously
inside the HTTP request handler.  With 2+ years of H1 data (~17k bars), the
computation takes longer than nginx's upstream timeout (typically 60–90s),
causing a 504 Gateway Timeout before the response is sent.

Even with a fast (mocked) signal evaluator, the per-bar overhead of:
  - get_window() slicing on every bar
  - update_cache() on every bar
  - _loop.run_until_complete() on every bar
accumulates to 60–120+ seconds on a realistic 2-year dataset.

This test reproduces the bug by:
  1. Generating a realistic 2-year full-day H1 dataset (~17k bars).
  2. Running BacktestEngine.run() with a near-instant mock evaluator.
  3. Asserting the run completes in < 30 seconds.

The test FAILS (reproducing the bug) because the engine is too slow for
synchronous HTTP use even with a mocked evaluator.

Fix: the backtest endpoint must submit work to a background task (Celery or
FastAPI BackgroundTasks) and return a job-id immediately, then the caller
polls a /backtest/status/{job_id} endpoint.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from anchor.backtesting.engine import BacktestEngine
from anchor.signals.engine import SignalResult

# ── Maximum acceptable latency for a synchronous HTTP endpoint ────────────────
# nginx default proxy_read_timeout is 60s.  We use 30s as a conservative
# threshold — well within nginx's limit even accounting for DB query overhead.
MAX_ACCEPTABLE_SECONDS = 30


def _make_full_day_h1_bars(
    start: datetime,
    n_years: float,
    base_price: float,
    atr: float = 0.001,
) -> pd.DataFrame:
    """
    Generate H1 bars for ALL trading hours across n_years years.

    Unlike the London-only helper in test_backtest_same_results.py, this
    covers all 24 hours per day so the dataset size matches what the API
    endpoint receives from the database (which stores full-day history).

    ~17,520 bars per year (365 days × 24 hours × ~2/3 non-weekend factor).
    """
    rows = []
    price = base_price
    t = start
    end = start + timedelta(days=int(365 * n_years))

    while t < end:
        if t.weekday() < 5:  # skip weekends
            price += atr * 0.05
            rows.append({
                "time":   t,
                "open":   price,
                "high":   price + atr,
                "low":    price - atr * 0.5,
                "close":  price + atr * 0.3,
                "volume": 1000.0,
            })
        t += timedelta(hours=1)

    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def _instant_signal(instrument: str) -> SignalResult:
    """A signal that always returns SUPPRESSED — minimal work per bar."""
    return SignalResult(
        instrument=instrument,
        direction=None,
        confluence_score=0.0,
        rsi_score=0.0,
        bb_kc_score=0.0,
        adx_score=0.0,
        sr_score=0.0,
        mtf_score=0.0,
        csi_score=0.0,
        ml_confidence=None,
        regime_state="RANGING",
        session="OFF",
        suppressed=True,
        suppression_reason="test_suppressed",
    )


class TestBacktestNotTooSlowForHTTP:
    """
    Reproduces the 504 bug: BacktestEngine.run() with a realistic 2-year
    dataset must complete in < 30 seconds when the signal evaluator is fast.

    If this test FAILS (elapsed > 30s), the synchronous HTTP approach is
    confirmed as the root cause — the engine is too slow to use inline in
    a request handler.
    """

    @pytest.mark.timeout(120)   # hard stop so CI doesn't hang
    def test_two_year_backtest_completes_within_http_timeout(self):
        """
        BUG REPRODUCTION: BacktestEngine.run() on a 2-year H1 dataset must
        finish in under 30 seconds with a near-instant mock evaluator.

        If this assertion fails, the engine's per-bar overhead (window slicing,
        cache updates, run_until_complete) is too high for synchronous HTTP use,
        confirming the 504 root cause.
        """
        start = datetime(2022, 1, 3, tzinfo=timezone.utc)  # first weekday of 2022
        df = _make_full_day_h1_bars(start, n_years=2.0, base_price=1.10)

        assert len(df) > 10_000, (
            f"Dataset too small to reproduce the bug: {len(df)} bars. "
            "Need at least 10k bars to trigger the timeout."
        )

        eng = BacktestEngine(initial_balance=10_000.0)
        eng.load_df("EUR_USD", "H1", df)

        with patch(
            "anchor.backtesting.engine.ConfluenceEngine.evaluate",
            new_callable=AsyncMock,
            side_effect=lambda instrument, dt: _instant_signal(instrument),
        ):
            t0 = time.perf_counter()
            eng.run("EUR_USD", "H1")
            elapsed = time.perf_counter() - t0

        assert elapsed < MAX_ACCEPTABLE_SECONDS, (
            f"BUG REPRODUCED: BacktestEngine.run() took {elapsed:.1f}s on a "
            f"{len(df)}-bar (2-year) dataset — exceeds the {MAX_ACCEPTABLE_SECONDS}s "
            f"HTTP timeout threshold.  The synchronous backtest endpoint will return "
            f"504 Gateway Timeout on multi-year data.\n\n"
            f"Fix: move the computation to a background task (Celery/BackgroundTasks) "
            f"and return a job_id immediately."
        )

    @pytest.mark.timeout(120)
    def test_one_year_backtest_completes_within_http_timeout(self):
        """
        Softer version: even a 1-year dataset must finish in < 30s.

        If 1 year also exceeds the limit, the per-bar overhead is extreme.
        """
        start = datetime(2023, 1, 2, tzinfo=timezone.utc)
        df = _make_full_day_h1_bars(start, n_years=1.0, base_price=1.10)

        eng = BacktestEngine(initial_balance=10_000.0)
        eng.load_df("EUR_USD", "H1", df)

        with patch(
            "anchor.backtesting.engine.ConfluenceEngine.evaluate",
            new_callable=AsyncMock,
            side_effect=lambda instrument, dt: _instant_signal(instrument),
        ):
            t0 = time.perf_counter()
            eng.run("EUR_USD", "H1")
            elapsed = time.perf_counter() - t0

        assert elapsed < MAX_ACCEPTABLE_SECONDS, (
            f"BUG REPRODUCED: even a 1-year backtest took {elapsed:.1f}s — "
            f"well over the {MAX_ACCEPTABLE_SECONDS}s HTTP limit.  "
            f"The synchronous endpoint approach is not viable."
        )
