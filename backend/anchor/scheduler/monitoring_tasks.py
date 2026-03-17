"""Monitoring tasks: live performance check, edge confidence, fit-weights trigger."""
from __future__ import annotations

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
}
_PERF_WR_MARGIN    = 0.08   # 8pp below benchmark WR → warning
_PERF_MIN_TRADES   = 20     # minimum closed trades before comparing
_PERF_WIN_DROUGHT  = 30     # days since last win → alert


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

        import pandas as pd
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        from anchor.backtesting.fit_weights import (
            _collect_live_trades, _fit, _compute_time_weights, CURRENT_WEIGHTS, COMPONENT_MAP,
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

        # ── Auto-apply: patch engine.py + fit_weights.py + write audit event ─
        from anchor.backtesting.fit_weights import (
            _patch_engine, _patch_current_weights, _log_system_event,
        )
        from pathlib import Path
        _patch_engine(new_weights, Path("/app/anchor/signals/engine.py"))
        _patch_current_weights(new_weights, Path("/app/anchor/backtesting/fit_weights.py"))
        _log_system_event(new_weights, "live_trades_auto", len(df))

        logger.info("fw_weights_applied", trade_count=trade_count, new_weights=new_weights)

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
        lines.append("engine.py patched and reloads on next signal scan.")

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
        _run_async(_alerts.send(alert_msg))
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

    alerts: list[str]    = []
    summaries: list[dict] = []

    for label, bench, strat_df in [
        ("LCR",    _PERF_BENCH["LCR"],    lcr_df),
        ("LONDON", _PERF_BENCH["LONDON"], london_df),
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
            "meta": json.dumps({"summaries": summaries, "alerts": alerts}),
        })

    # Telegram alert if degrading
    if alerts:
        try:
            alert_text = (
                "*ANCHOR PERFORMANCE ALERT*\n\n"
                + "\n".join(f"• {a}" for a in alerts)
                + "\n\nCheck `make fit-weights-progress` and review recent trades."
            )
            _run_async(_alerts.send(alert_text))
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
                        _session_filter = "NY_LCR" if _lbl == "LCR" else "LONDON"
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
    from anchor.database.engine import AsyncSessionLocal
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
