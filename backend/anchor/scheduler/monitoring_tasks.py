"""Monitoring tasks: live performance check, edge confidence, fit-weights trigger."""
from __future__ import annotations

from datetime import timezone

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async, _alerts

logger = structlog.get_logger(__name__)

# ── fit_weights trigger thresholds ───────────────────────────────────────────
# First trigger at 200 trades (minimum for reliable L1 regression).
# Re-triggers every 200 trades after that (400, 600, 800, ...).
# Does NOT auto-apply weights — sends Telegram alert with results for review.
_FW_FIRST_THRESHOLD = 200
_FW_RETRIGGER_EVERY = 200

# ── Live performance monitor ──────────────────────────────────────────────────
# Backtest benchmarks (8-year OOS validation results).
# Alert when rolling live metrics fall this far below benchmark.
_PERF_BENCH = {
    "LCR":    {"wr": 0.44, "pf": 1.40},   # worst qualifying LCR pair (GBP_USD)
    "LONDON": {"wr": 0.50, "pf": 1.20},   # London Trend OOS result
    # Conservative floor from stressed fix continuation validation.
    # Worst stressed survivor (GBP_USD): WR 61.6%, PF 2.219.
    "FIX":    {"wr": 0.61, "pf": 2.20},
    "NFP":    {"wr": 0.70, "pf": 1.50},
}
_PERF_WR_MARGIN    = 0.08   # 8pp below benchmark WR → warning
_PERF_MIN_TRADES   = 20     # minimum closed trades before comparing
_PERF_WIN_DROUGHT  = 30     # days since last win → alert


@celery_app.task(name="anchor.scheduler.jobs.score_fix_paper_signals", bind=True, max_retries=1)
def score_fix_paper_signals(self):
    """
    Backfill realized paper outcomes for matured LDN_FIX signals.

    For each non-suppressed fix signal whose expected 1h hold window has elapsed,
    compute:
      - actual first tradable entry price (open of the first bar at/after fix)
      - slippage proxy vs expected entry price
      - realized 1h return in pips
      - whether the continuation thesis completed positively

    Stored back into signal_metadata so the sleeve can be evaluated from real
    paper signals before any live execution path exists.
    """
    async def _inner():
        import pandas as pd
        from sqlalchemy import select, text as _text

        from anchor.database.engine import init_db
        import anchor.database.engine as _db_engine
        from anchor.database.models import Signal
        from anchor.database.repositories.market_data import MarketDataRepository
        from anchor.utils.math_utils import get_pip_size
        from anchor.utils.time_utils import utcnow

        await init_db()
        now = utcnow()
        scored = 0
        skipped = 0

        async with _db_engine.AsyncSessionFactory() as session:
            market_repo = MarketDataRepository(session)
            result = await session.execute(
                select(Signal)
                .where(
                    Signal.session == "LDN_FIX",
                    Signal.suppressed == False,  # noqa: E712
                )
                .order_by(Signal.created_at.desc())
                .limit(200)
            )
            signals = list(result.scalars().all())

            for signal in signals:
                meta = dict(signal.signal_metadata or {})
                if meta.get("paper_result_version") == 1:
                    continue

                exit_iso = meta.get("expected_exit_time_utc")
                fix_iso = meta.get("fix_time_utc")
                expected_entry = meta.get("entry_price")
                direction = signal.direction
                if not exit_iso or not fix_iso or expected_entry is None or direction not in {"LONG", "SHORT"}:
                    skipped += 1
                    continue

                exit_ts = pd.Timestamp(exit_iso)
                fix_ts = pd.Timestamp(fix_iso)
                if exit_ts.tzinfo is None:
                    exit_ts = exit_ts.tz_localize("UTC")
                else:
                    exit_ts = exit_ts.tz_convert("UTC")
                if fix_ts.tzinfo is None:
                    fix_ts = fix_ts.tz_localize("UTC")
                else:
                    fix_ts = fix_ts.tz_convert("UTC")

                if exit_ts.to_pydatetime() > now:
                    continue

                candles = await market_repo.get_candles(
                    signal.instrument,
                    "H1",
                    start=fix_ts.to_pydatetime(),
                    end=(exit_ts + pd.Timedelta(hours=1)).to_pydatetime(),
                    limit=10,
                )
                if len(candles) < 2:
                    skipped += 1
                    continue

                entry_bar = next((c for c in candles if c.time >= fix_ts.to_pydatetime()), None)
                exit_bar = next((c for c in candles if c.time >= exit_ts.to_pydatetime()), None)
                if entry_bar is None or exit_bar is None:
                    skipped += 1
                    continue

                pip = get_pip_size(signal.instrument)
                actual_entry = float(entry_bar.open)
                actual_exit = float(exit_bar.close)
                signed = 1.0 if direction == "LONG" else -1.0
                realized_ret_pips = signed * (actual_exit - actual_entry) / pip
                entry_slippage_pips = signed * (actual_entry - float(expected_entry)) / pip
                mfe_pips = signed * (float(exit_bar.high) - actual_entry) / pip
                mae_pips = signed * (float(exit_bar.low) - actual_entry) / pip
                if direction == "SHORT":
                    mfe_pips = signed * (float(exit_bar.low) - actual_entry) / pip
                    mae_pips = signed * (float(exit_bar.high) - actual_entry) / pip

                telemetry = {
                    "paper_result_version": 1,
                    "paper_scored_at": now.isoformat(),
                    "actual_entry_time_utc": entry_bar.time.isoformat(),
                    "actual_exit_time_utc": exit_bar.time.isoformat(),
                    "actual_entry_price": round(actual_entry, 5),
                    "actual_exit_price": round(actual_exit, 5),
                    "entry_slippage_pips": round(entry_slippage_pips, 2),
                    "realized_ret_pips": round(realized_ret_pips, 2),
                    "continuation_success": realized_ret_pips > 0,
                    "max_favorable_pips": round(mfe_pips, 2),
                    "max_adverse_pips": round(mae_pips, 2),
                }
                meta.update(telemetry)
                signal.signal_metadata = meta
                scored += 1

            await session.commit()

        if scored > 0:
            async with _db_engine.AsyncSessionFactory() as summary_session:
                rows = (await summary_session.execute(_text("""
                    SELECT
                        instrument,
                        COUNT(*) AS n_scored,
                        AVG(CAST(signal_metadata->>'realized_ret_pips' AS double precision)) AS mean_ret_pips,
                        AVG(CASE WHEN (signal_metadata->>'continuation_success')::boolean THEN 1.0 ELSE 0.0 END) AS wr,
                        AVG(CAST(signal_metadata->>'entry_slippage_pips' AS double precision)) AS avg_entry_slippage_pips
                    FROM signals
                    WHERE session = 'LDN_FIX'
                      AND suppressed = false
                      AND signal_metadata ? 'paper_result_version'
                    GROUP BY instrument
                    ORDER BY instrument
                """))).fetchall()
                pair_summary = [
                    {
                        "instrument": r[0],
                        "n_scored": int(r[1] or 0),
                        "mean_ret_pips": round(float(r[2] or 0.0), 2),
                        "win_rate": round(float(r[3] or 0.0) * 100.0, 1),
                        "avg_entry_slippage_pips": round(float(r[4] or 0.0), 2),
                    }
                    for r in rows
                ]
                await summary_session.execute(_text("""
                    INSERT INTO system_events
                        (event_at, event_type, severity, component, message, metadata)
                    VALUES
                        (NOW(), 'FIX_PAPER_SCORE', 'INFO', 'score_fix_paper_signals',
                         :msg, CAST(:meta AS jsonb))
                """), {
                    "msg": f"Scored {scored} fix paper signals",
                    "meta": __import__("json").dumps({
                        "scored": scored,
                        "skipped": skipped,
                        "pair_summary": pair_summary,
                    }),
                })
                await summary_session.commit()

        logger.info("fix_paper_signals_scored", scored=scored, skipped=skipped)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("fix_paper_signal_scoring_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.score_nfp_paper_signals", bind=True, max_retries=1)
def score_nfp_paper_signals(self):
    """Backfill realized paper outcomes for matured NFP_DRIFT signals."""
    async def _inner():
        import pandas as pd
        from sqlalchemy import select, text as _text

        from anchor.database.engine import init_db
        import anchor.database.engine as _db_engine
        from anchor.database.models import Signal
        from anchor.database.repositories.market_data import MarketDataRepository
        from anchor.utils.math_utils import get_pip_size
        from anchor.utils.time_utils import utcnow

        await init_db()
        now = utcnow()
        scored = 0
        skipped = 0

        async with _db_engine.AsyncSessionFactory() as session:
            market_repo = MarketDataRepository(session)
            result = await session.execute(
                select(Signal)
                .where(
                    Signal.session == "NFP_DRIFT",
                    Signal.suppressed == False,  # noqa: E712
                )
                .order_by(Signal.created_at.desc())
                .limit(100)
            )
            signals = list(result.scalars().all())

            for signal in signals:
                meta = dict(signal.signal_metadata or {})
                if meta.get("paper_result_version") == 1:
                    continue

                exit_iso = meta.get("expected_exit_time_utc")
                entry_iso = meta.get("entry_time_utc")
                direction = signal.direction
                if not exit_iso or not entry_iso or direction not in {"LONG", "SHORT"}:
                    skipped += 1
                    continue

                entry_ts = pd.Timestamp(entry_iso)
                exit_ts = pd.Timestamp(exit_iso)
                if entry_ts.tzinfo is None:
                    entry_ts = entry_ts.tz_localize("UTC")
                else:
                    entry_ts = entry_ts.tz_convert("UTC")
                if exit_ts.tzinfo is None:
                    exit_ts = exit_ts.tz_localize("UTC")
                else:
                    exit_ts = exit_ts.tz_convert("UTC")
                if exit_ts.to_pydatetime() > now:
                    continue

                candles = await market_repo.get_candles(
                    signal.instrument,
                    "H1",
                    start=entry_ts.to_pydatetime(),
                    end=(exit_ts + pd.Timedelta(hours=1)).to_pydatetime(),
                    limit=40,
                )
                if len(candles) < 2:
                    skipped += 1
                    continue

                entry_bar = next((c for c in candles if c.time >= entry_ts.to_pydatetime()), None)
                exit_bar = next((c for c in candles if c.time >= exit_ts.to_pydatetime()), None)
                if entry_bar is None or exit_bar is None:
                    skipped += 1
                    continue

                pip = get_pip_size(signal.instrument)
                actual_entry = float(entry_bar.open)
                actual_exit = float(exit_bar.close)
                signed = 1.0 if direction == "LONG" else -1.0
                realized_ret_pips = signed * (actual_exit - actual_entry) / pip
                mfe_pips = signed * (float(exit_bar.high) - actual_entry) / pip
                mae_pips = signed * (float(exit_bar.low) - actual_entry) / pip
                if direction == "SHORT":
                    mfe_pips = signed * (float(exit_bar.low) - actual_entry) / pip
                    mae_pips = signed * (float(exit_bar.high) - actual_entry) / pip

                meta.update({
                    "paper_result_version": 1,
                    "paper_scored_at": now.isoformat(),
                    "actual_entry_time_utc": entry_bar.time.isoformat(),
                    "actual_exit_time_utc": exit_bar.time.isoformat(),
                    "actual_entry_price": round(actual_entry, 5),
                    "actual_exit_price": round(actual_exit, 5),
                    "realized_ret_pips": round(realized_ret_pips, 2),
                    "continuation_success": realized_ret_pips > 0,
                    "max_favorable_pips": round(mfe_pips, 2),
                    "max_adverse_pips": round(mae_pips, 2),
                })
                signal.signal_metadata = meta
                scored += 1

            await session.commit()

        if scored > 0:
            async with _db_engine.AsyncSessionFactory() as summary_session:
                rows = (await summary_session.execute(_text("""
                    SELECT
                        instrument,
                        COUNT(*) AS n_scored,
                        AVG(CAST(signal_metadata->>'realized_ret_pips' AS double precision)) AS mean_ret_pips,
                        AVG(CASE WHEN (signal_metadata->>'continuation_success')::boolean THEN 1.0 ELSE 0.0 END) AS wr
                    FROM signals
                    WHERE session = 'NFP_DRIFT'
                      AND suppressed = false
                      AND signal_metadata ? 'paper_result_version'
                    GROUP BY instrument
                    ORDER BY instrument
                """))).fetchall()
                pair_summary = [
                    {
                        "instrument": r[0],
                        "n_scored": int(r[1] or 0),
                        "mean_ret_pips": round(float(r[2] or 0.0), 2),
                        "win_rate": round(float(r[3] or 0.0) * 100.0, 1),
                    }
                    for r in rows
                ]
                await summary_session.execute(_text("""
                    INSERT INTO system_events
                        (event_at, event_type, severity, component, message, metadata)
                    VALUES
                        (NOW(), 'NFP_PAPER_SCORE', 'INFO', 'score_nfp_paper_signals',
                         :msg, CAST(:meta AS jsonb))
                """), {
                    "msg": f"Scored {scored} NFP paper signals",
                    "meta": __import__("json").dumps({
                        "scored": scored,
                        "skipped": skipped,
                        "pair_summary": pair_summary,
                    }),
                })
                await summary_session.commit()

        logger.info("nfp_paper_signals_scored", scored=scored, skipped=skipped)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("nfp_paper_signal_scoring_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.check_fit_weights_trigger", bind=True, max_retries=1)
def check_fit_weights_trigger(self):
    """
    Daily check: when closed live trades cross 200 (then every 200 after),
    run the L1 weight regression analysis and send a Telegram alert with
    the results. Does NOT auto-apply — the user reviews and runs
    `make fit-weights-live --apply` to accept the new weights.

    Trigger logic:
      - Count closed trades with signal_id in DB (true OOS trades only).
      - Read last trigger count from system_events (FW_TRIGGER_RAN event).
      - Fire when: count >= 200 AND count // 200 > last_count // 200.
      - Always write FW_TRIGGER_CHECKED event with current count (progress log).
    """
    import json
    from sqlalchemy import create_engine, text
    from anchor.config import get_settings

    cfg = get_settings()
    db  = create_engine(cfg.sync_database_url)

    # ── Step 1: count closed OOS trades ──────────────────────────────────────
    with db.connect() as conn:
        trade_count = conn.execute(text(
            "SELECT COUNT(*) FROM trades "
            "WHERE closed_at IS NOT NULL AND signal_id IS NOT NULL"
        )).scalar() or 0

        # Last count at which the regression was run
        row = conn.execute(text(
            "SELECT metadata FROM system_events "
            "WHERE event_type = 'FW_TRIGGER_RAN' "
            "ORDER BY event_at DESC LIMIT 1"
        )).fetchone()

    last_trigger_count = 0
    if row and row[0]:
        try:
            last_trigger_count = int(row[0].get("trade_count", 0))
        except (TypeError, AttributeError, ValueError):
            last_trigger_count = 0

    logger.info(
        "fw_trigger_checked",
        trade_count=trade_count,
        last_trigger_count=last_trigger_count,
        threshold=_FW_FIRST_THRESHOLD,
    )

    # ── Step 2: write progress event (always — creates visible log) ──────────
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'FW_TRIGGER_CHECKED', 'INFO', 'fit_weights',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "msg":  f"Trade count: {trade_count} / next trigger at "
                    f"{((trade_count // _FW_RETRIGGER_EVERY) + 1) * _FW_RETRIGGER_EVERY}",
            "meta": json.dumps({
                "trade_count":       trade_count,
                "last_trigger_count": last_trigger_count,
                "next_trigger":      (
                    (trade_count // _FW_RETRIGGER_EVERY) + 1
                ) * _FW_RETRIGGER_EVERY,
            }),
        })

    # ── Step 3: decide whether to trigger ────────────────────────────────────
    if trade_count < _FW_FIRST_THRESHOLD:
        return  # not enough trades yet

    current_band = trade_count // _FW_RETRIGGER_EVERY
    last_band    = last_trigger_count // _FW_RETRIGGER_EVERY
    if current_band <= last_band:
        return  # already ran at this band

    # ── Step 4: run regression analysis ──────────────────────────────────────
    logger.info("fw_trigger_firing", trade_count=trade_count)

    try:
        import warnings
        import logging as _logging
        _logging.disable(_logging.CRITICAL)
        warnings.filterwarnings("ignore")


        from anchor.backtesting.fit_weights import (
            _collect_live_trades, _fit, _compute_time_weights, CURRENT_WEIGHTS,
        )

        df = _collect_live_trades()
        if len(df) < 50:
            logger.warning("fw_trigger_too_few_trades", count=len(df))
            return

        # Time-weighted: recent trades carry more weight (90-day half-life).
        # Adapts to current regime without requiring full RL retraining.
        sample_weight = _compute_time_weights(df, half_life_days=90.0)
        new_weights = _fit(df, C=1.0, sample_weight=sample_weight)
        logger.info("fw_time_weighted_fit", trade_count=len(df), half_life_days=90.0)

        logger.info("fw_weights_recommended", trade_count=trade_count, new_weights=new_weights)

        # Build Telegram summary
        wins = int((df["outcome"] == 1).sum())
        lines = [
            "*Weights Auto-Updated*",
            f"Trades: {len(df)}  WR: {wins/len(df)*100:.1f}%",
            "",
            "```",
            f"{'Component':<22} {'Old':>7} {'New':>7} {'Delta':>7}",
            "-" * 48,
        ]
        for k, old_v in CURRENT_WEIGHTS.items():
            new_v = new_weights.get(k, 0.0)
            delta = new_v - old_v
            sign  = "+" if delta >= 0 else ""
            lines.append(f"{k:<22} {old_v:.4f}  {new_v:.4f}  {sign}{delta:.4f}")
        lines.append("```")
        lines.append("No code was patched automatically.")
        lines.append("Review before applying to production.")

        alert_msg = "\n".join(lines)

    except Exception as exc:
        logger.error("fw_trigger_regression_failed", error=str(exc))
        alert_msg = (
            f"*Weight Optimizer Failed* — {trade_count} trades in DB\n"
            f"Error: {exc}\n"
            f"Run manually: `make fit-weights-live-apply`"
        )
        new_weights = {}

    # ── Step 5: send Telegram alert ───────────────────────────────────────────
    try:
        _run_async(_alerts.send_info(alert_msg))
    except Exception as exc:
        logger.warning("fw_trigger_alert_failed", error=str(exc))

    # ── Step 6: write FW_TRIGGER_RAN event (prevents re-firing this band) ────
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'FW_TRIGGER_RAN', 'INFO', 'fit_weights',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "msg":  f"Regression run at {trade_count} trades",
            "meta": json.dumps({
                "trade_count":  trade_count,
                "new_weights":  new_weights,
                "old_weights":  CURRENT_WEIGHTS,
            }),
        })


@celery_app.task(name="anchor.scheduler.jobs.monitor_live_performance", bind=True, max_retries=1)
def monitor_live_performance(self):
    """
    Daily check: compare actual live trade performance vs backtested benchmarks.

    Computes rolling WR and PF over the last 50 closed trades per strategy.
    Alerts via Telegram if actual performance degrades below OOS expectations.

    Alert triggers:
      - Rolling WR < backtest_WR - 8pp  (strategy losing its edge)
      - Rolling PF < 1.0               (strategy actively losing money)
      - 30+ days since last winning trade (win drought)
    """
    import json
    from sqlalchemy import create_engine, text as _text
    import pandas as _pd
    from anchor.config import get_settings

    cfg = get_settings()
    db  = create_engine(cfg.sync_database_url)

    with db.connect() as conn:
        rows = conn.execute(_text("""
            SELECT
                t.closed_at,
                t.net_pl,
                CAST(t.net_pl AS float) /
                    NULLIF(CAST(t.entry_price AS float), 0) * 100 AS pl_pct,
                t.instrument,
                s.session,
                s.confluence_score,
                t.signal_id
            FROM trades t
            LEFT JOIN signals s ON s.id = t.signal_id
            WHERE t.closed_at IS NOT NULL
            ORDER BY t.closed_at DESC
            LIMIT 200
        """)).fetchall()

    if not rows:
        logger.info("live_perf_monitor_skip", reason="no_closed_trades")
        return

    df = _pd.DataFrame(
        rows,
        columns=["closed_at", "net_pl", "pl_pct", "instrument",
                 "session", "confluence_score", "signal_id"],
    )
    df["closed_at"] = _pd.to_datetime(df["closed_at"], utc=True)
    df["is_win"]    = df["pl_pct"] > 0

    # Split by strategy via session tag
    lcr_df    = df[df["session"] == "NY_LCR"].head(50)
    london_df = df[df["session"].isin(["LONDON"])].head(50)
    fix_df    = df[df["session"] == "LDN_FIX"].head(50)

    alerts: list[str]    = []
    summaries: list[dict] = []

    for label, bench, strat_df in [
        ("LCR",    _PERF_BENCH["LCR"],    lcr_df),
        ("LONDON", _PERF_BENCH["LONDON"], london_df),
        ("FIX",    _PERF_BENCH["FIX"],    fix_df),
    ]:
        n = len(strat_df)
        if n < _PERF_MIN_TRADES:
            summaries.append({"strategy": label, "n": n, "status": "insufficient_trades"})
            continue

        wr = float(strat_df["is_win"].mean())
        wins   = strat_df[strat_df["is_win"]]
        losses = strat_df[~strat_df["is_win"]]
        gross_wins   = float(wins["pl_pct"].sum())
        gross_losses = abs(float(losses["pl_pct"].sum()))
        pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")

        # Days since last win
        last_win_ts = strat_df[strat_df["is_win"]]["closed_at"].max()
        neg_streak_days = 0
        if _pd.notna(last_win_ts):
            neg_streak_days = (_pd.Timestamp.utcnow() - last_win_ts).days

        summary: dict = {
            "strategy":         label,
            "n_trades":         n,
            "rolling_wr_pct":   round(wr * 100, 1),
            "bench_wr_pct":     round(bench["wr"] * 100, 1),
            "rolling_pf":       round(pf, 3) if pf != float("inf") else 9.999,
            "bench_pf":         bench["pf"],
            "days_since_win":   neg_streak_days,
            "status":           "ok",
        }

        if wr < (bench["wr"] - _PERF_WR_MARGIN):
            gap = (bench["wr"] - wr) * 100
            alerts.append(
                f"{label}: rolling WR {wr*100:.1f}% is {gap:.1f}pp below "
                f"backtest benchmark {bench['wr']*100:.0f}% (last {n} trades)"
            )
            summary["status"] = "DEGRADED_WR"

        if pf < 1.0:
            alerts.append(
                f"{label}: rolling PF {pf:.3f} < 1.0 (last {n} trades) — "
                f"strategy is net-negative, review immediately"
            )
            summary["status"] = "UNPROFITABLE"

        if neg_streak_days >= _PERF_WIN_DROUGHT:
            alerts.append(
                f"{label}: {neg_streak_days} days since last winning trade — "
                f"possible regime change, consider pausing"
            )
            summary["status"] = "WIN_DROUGHT"

        summaries.append(summary)
        logger.info("live_perf_monitor", **summary)

    # ── Per-pair 60-day PF breakdown by sleeve ───────────────────────────────
    # Shows which pairs are approaching circuit-breaker territory.
    pair_pf_60d: list[dict] = []
    with db.connect() as conn:
        for _strategy, _session_filter, _pairs in [
            ("LCR", "NY_LCR", ["EUR_USD", "NZD_USD", "AUD_USD", "EUR_JPY", "USD_CAD"]),
            ("FIX", "LDN_FIX", ["USD_JPY", "EUR_USD", "GBP_USD"]),
        ]:
            for _pair in _pairs:
                _pair_rows = conn.execute(_text(
                    "SELECT t.net_pl FROM trades t "
                    "LEFT JOIN signals s ON s.id = t.signal_id "
                    "WHERE t.instrument = :inst "
                    "AND t.signal_id IS NOT NULL "
                    "AND s.session = :session "
                    "AND t.closed_at >= NOW() - INTERVAL '60 days'"
                ), {"inst": _pair, "session": _session_filter}).fetchall()
                _n = len(_pair_rows)
                if _n > 0:
                    _gw = sum(r[0] for r in _pair_rows if r[0] > 0)
                    _gl = abs(sum(r[0] for r in _pair_rows if r[0] <= 0))
                    _pf = round(_gw / _gl, 3) if _gl > 0 else None
                    _cb_active = _pf is not None and _pf < 1.0 and _n >= 15
                    pair_pf_60d.append({
                        "strategy": _strategy,
                        "pair": _pair,
                        "trades": _n,
                        "pf_60d": _pf,
                        "circuit_breaker": _cb_active,
                    })

    # Write to system_events
    severity = "WARNING" if alerts else "INFO"
    message  = "; ".join(alerts) if alerts else "Live performance within benchmarks"
    with db.begin() as conn:
        conn.execute(_text("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'LIVE_PERF_CHECK', :sev, 'monitor_live_performance',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "sev":  severity,
            "msg":  message,
            "meta": json.dumps({
                "summaries": summaries,
                "alerts": alerts,
                "pair_pf_60d": pair_pf_60d,
            }),
        })

    # Telegram alert if degrading
    if alerts:
        try:
            alert_text = (
                "*ANCHOR PERFORMANCE ALERT*\n\n"
                + "\n".join(f"• {a}" for a in alerts)
                + "\n\nCheck `make fit-weights-progress` and review recent trades."
            )
            _run_async(_alerts.send_warning(alert_text))
        except Exception as exc:
            logger.warning("live_perf_alert_failed", error=str(exc))

        # AI diagnosis for each degraded strategy
        async def _run_drift_diagnoses():
            import redis.asyncio as _r
            from anchor.intelligence.trade_intelligence import diagnose_parameter_drift as _diagnose
            cfg2 = get_settings()
            _redis = _r.from_url(cfg2.redis_url, decode_responses=True)
            try:
                for s in summaries:
                    if s.get("status") in ("DEGRADED_WR", "UNPROFITABLE", "WIN_DROUGHT"):
                        _lbl = s["strategy"]
                        _bench = _PERF_BENCH.get(_lbl, {})
                        # Gather per-pair breakdown from raw trades for this strategy
                        _session_filter = {
                            "LCR": "NY_LCR",
                            "LONDON": "LONDON",
                            "FIX": "LDN_FIX",
                        }.get(_lbl, "LONDON")
                        _strat_rows = [
                            {
                                "instrument":  r[3],
                                "net_pl":      float(r[1]) if r[1] is not None else 0.0,
                                "pl_pct":      float(r[2]) if r[2] is not None else 0.0,
                                "confluence":  float(r[5]) if r[5] is not None else None,
                                "closed_at":   str(r[0]),
                            }
                            for r in rows if r[4] == _session_filter
                        ]
                        _diagnosis, _ = await _diagnose(_lbl, s, _bench, _strat_rows[:30])
                        if _diagnosis:
                            await _alerts.send_warning(
                                f"🧠 Drift Diagnosis — {_lbl}\n\n{_diagnosis}"
                            )
            finally:
                await _redis.aclose()

        try:
            _run_async(_run_drift_diagnoses())
        except Exception as _dd_exc:
            logger.warning("drift_diagnosis_failed", error=str(_dd_exc))


@celery_app.task(name="anchor.scheduler.jobs.assess_edge_confidence", bind=True, max_retries=1)
def assess_edge_confidence(self):
    """
    Daily assessment of whether the macro environment supports Anchor's
    session-based structural edges.

    Runs three signals:
      1. Session character  — London session trendiness vs choppiness
      2. Pair correlation   — breakdown between normally-correlated pairs
      3. Macro stress       — VIX acceleration + economic surprise extremes

    Output: EDGE_CONFIDENCE_CHECK system event + Telegram alert on state change
    or when confidence is REDUCED/LOW.

    Runs daily after London close (18:00 UTC). Not a trading decision —
    a signal to the human operator to review the environment.
    """
    import json as _json
    from sqlalchemy import create_engine as _ce, text as _t
    import redis.asyncio as _redis_async
    from anchor.config import get_settings
    from anchor.database.engine import AsyncSessionFactory as AsyncSessionLocal
    from anchor.monitoring.edge_confidence import assess_edge_confidence as _assess, build_alert_message

    cfg = get_settings()
    db  = _ce(cfg.sync_database_url)

    # ── Load previous confidence level ────────────────────────────────────────
    previous_confidence: str | None = None
    with db.connect() as conn:
        row = conn.execute(_t("""
            SELECT metadata FROM system_events
            WHERE event_type = 'EDGE_CONFIDENCE_CHECK'
            ORDER BY event_at DESC LIMIT 1
        """)).fetchone()
        if row and row[0]:
            try:
                previous_confidence = row[0].get("confidence")
            except (AttributeError, TypeError):
                pass

    # ── Run assessment ────────────────────────────────────────────────────────
    async def _run():
        redis_client = _redis_async.from_url(cfg.redis_url, decode_responses=True)
        try:
            async with AsyncSessionLocal() as db_session:
                return await _assess(db_session, redis_client, previous_confidence)
        finally:
            await redis_client.aclose()

    result = _run_async(_run())

    # ── Write system event ────────────────────────────────────────────────────
    severity = {"HIGH": "INFO", "REDUCED": "WARNING", "LOW": "CRITICAL"}.get(result.confidence, "INFO")
    with db.begin() as conn:
        conn.execute(_t("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'EDGE_CONFIDENCE_CHECK', :sev, 'edge_confidence',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "sev":  severity,
            "msg":  f"Edge confidence: {result.confidence} ({result.flag_count} flag(s))",
            "meta": _json.dumps(result.to_dict()),
        })

    logger.info(
        "edge_confidence_assessed",
        confidence=result.confidence,
        flags=result.flag_count,
        previous=previous_confidence,
    )

    # ── Alert on state change or non-HIGH confidence ──────────────────────────
    state_changed   = previous_confidence and previous_confidence != result.confidence
    is_degraded     = result.confidence in ("REDUCED", "LOW")
    should_alert    = state_changed or is_degraded

    if should_alert:
        try:
            msg = build_alert_message(result)
            if result.confidence == "LOW":
                _run_async(_alerts.send_critical(msg))
            elif result.confidence == "REDUCED":
                _run_async(_alerts.send_warning(msg))
            else:
                _run_async(_alerts.send_info(msg))
        except Exception as exc:
            logger.warning("edge_confidence_alert_failed", error=str(exc))


@celery_app.task(name="anchor.scheduler.jobs.snapshot_lcr_pair_status", bind=True, max_retries=1)
def snapshot_lcr_pair_status(self):
    """
    Daily snapshot of LCR pair statuses written to system_events.

    Runs at 21:05 UTC (after the 17-20 UTC LCR session closes).
    Idempotent: skips if a snapshot already exists for today's UTC date.

    event_type = LCR_PAIR_STATUS_SNAPSHOT
    severity   = INFO / WARN / ERROR depending on worst pair status
    metadata   = { pairs: [...], summary: {active, watchlist, disabled} }

    Use GET /system/lcr-pair-status/history to retrieve the log.
    """
    import json
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import create_engine, text
    from anchor.config import get_settings
    cfg = get_settings()
    db  = create_engine(cfg.sync_database_url)

    # ── Deduplication: skip if already ran today ──────────────────────────────
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with db.connect() as conn:
        already_ran = conn.execute(text("""
            SELECT 1 FROM system_events
            WHERE event_type = 'LCR_PAIR_STATUS_SNAPSHOT'
              AND event_at >= :today
            LIMIT 1
        """), {"today": today_start}).fetchone()

    if already_ran:
        logger.info("lcr_pair_status_snapshot_skip", reason="already_ran_today")
        return

    # ── Load current pair statuses ────────────────────────────────────────────
    async def _load():
        from anchor.database.engine import init_db
        import anchor.database.engine as _eng
        from anchor.signals.lcr_pair_status import load_lcr_pair_statuses
        if _eng.AsyncSessionFactory is None:
            await init_db()
        async with _eng.AsyncSessionFactory() as session:
            return await load_lcr_pair_statuses(session)

    pair_statuses = _run_async(_load())

    # ── Build payload ─────────────────────────────────────────────────────────
    counts = {"active": 0, "watchlist": 0, "disabled": 0}
    pairs_payload = []
    for r in pair_statuses.values():
        counts[r.status.value] += 1
        pairs_payload.append({
            "instrument": r.instrument,
            "status":     r.status.value,
            "reasons":    r.reasons,
            "metrics":    r.metrics,
        })

    summary = counts
    n = len(pairs_payload)
    message = (
        f"{n} pairs: {counts['active']} active, "
        f"{counts['watchlist']} watchlist, {counts['disabled']} disabled"
    )

    if counts["disabled"] > 0:
        severity = "ERROR"
    elif counts["watchlist"] > 0:
        severity = "WARN"
    else:
        severity = "INFO"

    # ── Write event ───────────────────────────────────────────────────────────
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO system_events
                (event_at, event_type, severity, component, message, metadata)
            VALUES
                (NOW(), 'LCR_PAIR_STATUS_SNAPSHOT', :sev, 'lcr_pair_status',
                 :msg, CAST(:meta AS jsonb))
        """), {
            "sev":  severity,
            "msg":  message,
            "meta": json.dumps({"pairs": pairs_payload, "summary": summary}),
        })

    logger.info(
        "lcr_pair_status_snapshot_written",
        active=counts["active"],
        watchlist=counts["watchlist"],
        disabled=counts["disabled"],
    )

    # ── Alert if any pair just became DISABLED ────────────────────────────────
    disabled_pairs = [r["instrument"] for r in pairs_payload if r["status"] == "disabled"]
    if disabled_pairs:
        try:
            alert = (
                "*LCR PAIR DISABLED*\n\n"
                + "\n".join(
                    f"• {r['instrument'].replace('_', '/')}: "
                    + ", ".join(r["reasons"])
                    for r in pairs_payload if r["status"] == "disabled"
                )
                + "\n\nPair will be skipped automatically until trade metrics recover."
            )
            _run_async(_alerts.send_warning(alert))
        except Exception as exc:
            logger.warning("lcr_pair_disabled_alert_failed", error=str(exc))
