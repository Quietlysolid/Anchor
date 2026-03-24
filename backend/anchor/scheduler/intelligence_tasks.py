"""AI intelligence tasks: briefs, debriefs, anomaly checks, trade explanations, journal analysis."""
from __future__ import annotations

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async, _alerts

logger = structlog.get_logger(__name__)


def _run_intelligence_brief(report_type: str) -> None:
    """Shared async runner for all three brief types."""
    async def _inner():
        import redis.asyncio as aioredis
        from anchor.config import settings
        from anchor.database.engine import init_db, get_session
        from anchor.database.models import IntelligenceReport
        from anchor.intelligence.context_builder import build_context
        from anchor.intelligence.report_generator import (
            generate_presession_brief,
            generate_postsession_debrief,
            generate_weekly_synthesis,
        )

        if not settings.anthropic_api_key:
            logger.warning("intelligence_skipped_no_api_key", report_type=report_type)
            return

        await init_db()
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

        try:
            async with get_session() as session:
                ctx = await build_context(report_type, session, redis_client)

                if report_type == "PRESESSION":
                    content, tokens = await generate_presession_brief(ctx)
                    # Tier 1: generate structured session quality alongside the brief
                    # Written to Redis 'session_quality' (TTL 8h) so engine + sizer can read it
                    try:
                        import json as _json
                        from anchor.intelligence.session_quality import (
                            generate_session_quality,
                            _REDIS_KEY as _SQ_KEY,
                            _TTL_SECONDS as _SQ_TTL,
                        )
                        sq = await generate_session_quality(ctx)
                        await redis_client.set(_SQ_KEY, _json.dumps(sq), ex=_SQ_TTL)
                        logger.info(
                            "session_quality_cached",
                            environment=sq["environment"],
                            confidence=sq["confidence"],
                            size_scale=sq["size_scale"],
                            threshold_adj=sq["threshold_adjustment"],
                        )
                    except Exception as _sq_exc:
                        logger.warning("session_quality_write_failed", error=str(_sq_exc))
                elif report_type == "POSTSESSION":
                    content, tokens = await generate_postsession_debrief(ctx)
                    # Tier 2b: rolling session score (0–10 edge quality, last 5 sessions)
                    try:
                        from anchor.intelligence.report_generator import score_postsession as _score_ps
                        _score = await _score_ps(content)
                        await redis_client.lpush("rolling_session_scores", _score)
                        await redis_client.ltrim("rolling_session_scores", 0, 4)
                        _scores_raw = await redis_client.lrange("rolling_session_scores", 0, -1)
                        _scores = [float(s) for s in _scores_raw]
                        _rolling_avg = sum(_scores) / len(_scores) if _scores else 5.0
                        await redis_client.set("rolling_score_avg", _rolling_avg, ex=7 * 86_400)
                        logger.info(
                            "rolling_session_score_updated",
                            score=_score,
                            rolling_avg=round(_rolling_avg, 2),
                            n=len(_scores),
                        )
                    except Exception as _rs_exc:
                        logger.warning("rolling_session_score_failed", error=str(_rs_exc))
                    # Tier 2c: macro anomaly detection — flags price vs macro dissonance per pair
                    try:
                        from anchor.intelligence.anomaly_detector import detect_macro_anomaly
                        _dissonant = await detect_macro_anomaly(ctx, redis_client)
                        if _dissonant:
                            logger.info("macro_anomaly_pairs_flagged", pairs=_dissonant)
                    except Exception as _ma_exc:
                        logger.warning("macro_anomaly_task_failed", error=str(_ma_exc))
                else:
                    content, tokens = await generate_weekly_synthesis(ctx)
                    # Tier 2b: reset rolling scores on weekly synthesis (fresh week slate)
                    try:
                        await redis_client.delete("rolling_session_scores", "rolling_score_avg")
                        logger.info("rolling_session_scores_reset")
                    except Exception as _rs_exc:
                        logger.warning("rolling_session_scores_reset_failed", error=str(_rs_exc))

                report = IntelligenceReport(
                    report_type=report_type,
                    content=content,
                    context_snapshot=ctx,
                    tokens_used=tokens,
                )
                session.add(report)
                await session.commit()
                await session.refresh(report)

            # Deliver via Telegram (truncate at 4096 chars)
            header = {
                "PRESESSION": "📊 PRE-SESSION BRIEF",
                "POSTSESSION": "📋 POST-SESSION DEBRIEF",
                "WEEKLY": "📈 WEEKLY SYNTHESIS",
            }[report_type]
            await _alerts.send_info(f"{header}\n\n{content[:3900]}")

            # Mark delivered
            async with get_session() as session:
                r = await session.get(IntelligenceReport, report.id)
                if r:
                    r.delivered_telegram = True
                    await session.commit()

            logger.info("intelligence_brief_complete", report_type=report_type, tokens=tokens)

        except Exception as exc:
            logger.error("intelligence_brief_failed", report_type=report_type, error=str(exc))
        finally:
            await redis_client.aclose()

    _run_async(_inner())


@celery_app.task(name="anchor.scheduler.jobs.generate_presession_brief", bind=True, max_retries=2)
def generate_presession_brief(self):
    """Generate and deliver the London pre-session brief at 06:30 UTC (Mon–Fri)."""
    try:
        _run_intelligence_brief("PRESESSION")
    except Exception as exc:
        logger.error("presession_brief_task_error", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.generate_postsession_debrief", bind=True, max_retries=2)
def generate_postsession_debrief(self):
    """Generate and deliver the post-London-session debrief at 12:30 UTC (Mon–Fri)."""
    try:
        _run_intelligence_brief("POSTSESSION")
    except Exception as exc:
        logger.error("postsession_debrief_task_error", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.generate_weekly_synthesis", bind=True, max_retries=2)
def generate_weekly_synthesis(self):
    """Generate and deliver the weekly synthesis at 22:00 UTC on Sundays."""
    try:
        _run_intelligence_brief("WEEKLY")
    except Exception as exc:
        logger.error("weekly_synthesis_task_error", error=str(exc))
        raise self.retry(exc=exc, countdown=600)


@celery_app.task(name="anchor.scheduler.jobs.run_intrabar_anomaly_check", bind=True, max_retries=1)
def run_intrabar_anomaly_check(self):
    """
    Hourly intrabar macro dissonance check during London session (07:05–11:05 UTC Mon–Fri).

    Fetches the last 4 H1 bars per pair + macro snapshot from Redis, then asks
    Claude Haiku whether current price action contradicts the macro backdrop.
    Flags are written as macro_dissonance:{pair} with a 2-hour TTL — the same
    Redis key the signal engine already reads for the -0.05 confluence penalty.
    The postsession check at 12:30 UTC owns clearing stale flags.
    """
    import json as _json
    from datetime import datetime, timezone as _tz
    import redis.asyncio as _redis_async
    from anchor.config import get_settings as _get_settings
    from anchor.database.engine import get_session as _get_session_ia
    from anchor.database.repositories.market_data import MarketDataRepository
    from anchor.intelligence.anomaly_detector import detect_intrabar_anomaly as _detect

    cfg = _get_settings()

    async def _inner():
        now_utc = datetime.now(_tz.utc)
        if not (7 <= now_utc.hour < 12):
            logger.info("intrabar_anomaly_skipped_outside_london", hour=now_utc.hour)
            return

        redis_client = _redis_async.from_url(cfg.redis_url, decode_responses=True)
        try:
            # ── Macro snapshot from Redis ─────────────────────────────────────
            async def _rget(key: str):
                try:
                    raw = await redis_client.get(key)
                    return _json.loads(raw) if raw else None
                except Exception:
                    return None

            macro: dict = {}
            for rkey, label in [
                ("vix_data",         "vix"),
                ("fred_rate_diff",   "rate_differentials"),
                ("cross_asset_risk", "cross_asset"),
                ("cot_data",         "cot_positioning"),
            ]:
                val = await _rget(rkey)
                if val is not None:
                    macro[label] = val

            surprises = {}
            for ccy in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD"]:
                val = await _rget(f"econ_surprise:{ccy}")
                if val is not None:
                    surprises[ccy] = val
            if surprises:
                macro["economic_surprise"] = surprises

            # ── Last 4 H1 bars per pair ───────────────────────────────────────
            pairs_bars: dict = {}
            async with _get_session_ia() as session:
                repo = MarketDataRepository(session)
                for pair in cfg.instruments:
                    candles = await repo.get_latest_n_candles(pair, "H1", 4)
                    pairs_bars[pair] = [
                        {
                            "time":  str(c.time),
                            "open":  float(c.open),
                            "high":  float(c.high),
                            "low":   float(c.low),
                            "close": float(c.close),
                        }
                        for c in candles
                    ]

            if not any(pairs_bars.values()):
                logger.warning("intrabar_anomaly_no_candle_data")
                return

            dissonant = await _detect(pairs_bars, macro, redis_client)

            if dissonant:
                msg = (
                    f"\u26a0\ufe0f Intrabar dissonance {now_utc.strftime('%H:%M')} UTC\n"
                    f"Pairs: {', '.join(dissonant)}\n"
                    f"Confluence penalty active — new entries suppressed on flagged pairs."
                )
                await _alerts.send_warning(msg)

        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("intrabar_anomaly_check_task_error", error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="anchor.scheduler.jobs.generate_trade_explanations", bind=True, max_retries=1)
def generate_trade_explanations(self):
    """
    Generate Claude Haiku explanations for recently closed trades (every 30 min).

    Finds trades closed in the last 35 minutes that have no entry in
    intelligence_reports with report_type='TRADE_EXPLANATION'. For each,
    fetches the linked Signal row + macro context from Redis, generates a
    2-3 sentence explanation, and saves as IntelligenceReport.
    """
    import json as _json
    import redis.asyncio as _redis_async
    from datetime import datetime, timedelta, timezone as _tz
    from anchor.config import get_settings as _get_settings
    from anchor.database.models import IntelligenceReport, Signal
    from anchor.intelligence.trade_intelligence import explain_trade as _explain
    from sqlalchemy import select as _sel, text as _sqlt

    cfg = _get_settings()

    async def _inner():
        from anchor.database.engine import init_db as _init_db
        await _init_db()
        redis_client = _redis_async.from_url(cfg.redis_url, decode_responses=True)
        try:
            # Macro snapshot from Redis
            macro: dict = {}
            for _rk, _rl in [
                ("vix_data",         "vix"),
                ("fred_rate_diff",   "rate_differentials"),
                ("cross_asset_risk", "cross_asset"),
                ("cot_interpretation", "cot_bias"),
            ]:
                try:
                    _rv = await redis_client.get(_rk)
                    if _rv:
                        macro[_rl] = _json.loads(_rv)
                except Exception as _exc:
                    logger.debug("intel_tasks_macro_redis_read_failed", key=_rk, error=str(_exc))

            # Look back 30 days — catches historical trades without explanations
            cutoff = datetime.now(_tz.utc) - timedelta(days=30)

            from anchor.database.engine import get_session as _get_session
            async with _get_session() as session:
                # All trades in the window, most recent first
                trade_rows = (await session.execute(_sqlt("""
                    SELECT t.id, t.instrument, t.direction,
                           CAST(t.entry_price AS float), CAST(t.exit_price AS float),
                           CAST(t.net_pl AS float),
                           COALESCE(CAST(t.net_pl AS float) /
                               NULLIF(CAST(t.entry_price AS float), 0) * 100, 0)
                               AS pl_pct,
                           t.duration_minutes, t.close_reason,
                           t.regime_at_entry, t.session_at_entry,
                           t.signal_id, t.closed_at
                    FROM trades t
                    WHERE t.closed_at >= :cutoff
                    ORDER BY t.closed_at DESC
                    LIMIT 20
                """), {"cutoff": cutoff})).fetchall()

                if not trade_rows:
                    return

                # Find all existing explanations (no time filter — prevents duplicates)
                ex_rows = (await session.execute(_sqlt("""
                    SELECT context_snapshot->>'trade_id'
                    FROM intelligence_reports
                    WHERE report_type = 'TRADE_EXPLANATION'
                """))).fetchall()
                existing_ids = {r[0] for r in ex_rows if r[0]}

                for row in trade_rows:
                    trade_id = str(row[0])
                    if trade_id in existing_ids:
                        continue

                    trade_data = {
                        "id":              trade_id,
                        "instrument":      row[1],
                        "direction":       row[2],
                        "entry_price":     row[3],
                        "exit_price":      row[4],
                        "net_pl":          row[5],
                        "pl_pct":          round(row[6], 4),
                        "duration_minutes": row[7],
                        "close_reason":    row[8],
                        "regime_at_entry": row[9],
                        "session_at_entry": row[10],
                        "closed_at":       str(row[12]),
                    }
                    outcome = "WIN" if row[5] > 0 else "LOSS"

                    # Fetch linked signal
                    signal_data: dict | None = None
                    if row[11]:  # signal_id
                        try:
                            sig = (await session.execute(
                                _sel(Signal).where(Signal.id == row[11])
                            )).scalar_one_or_none()
                            if sig:
                                signal_data = {
                                    "confluence_score": float(sig.confluence_score),
                                    "rsi_score":   float(sig.rsi_score)   if sig.rsi_score   else None,
                                    "bb_kc_score": float(sig.bb_kc_score) if sig.bb_kc_score else None,
                                    "adx_score":   float(sig.adx_score)   if sig.adx_score   else None,
                                    "sr_score":    float(sig.sr_score)    if sig.sr_score    else None,
                                    "mtf_score":   float(sig.mtf_score)   if sig.mtf_score   else None,
                                    "regime":      sig.regime_state,
                                    "session":     sig.session,
                                    "metadata":    sig.signal_metadata or {},
                                }
                        except Exception as _exc:
                            logger.debug("intel_tasks_signal_read_failed", error=str(_exc))

                    explanation, tokens = await _explain(trade_data, signal_data, macro)
                    if not explanation:
                        continue

                    report = IntelligenceReport(
                        report_type="TRADE_EXPLANATION",
                        content=explanation,
                        context_snapshot={
                            "trade_id":   trade_id,
                            "instrument": row[1],
                            "outcome":    outcome,
                            "net_pl":     row[5],
                            "session":    row[10],
                        },
                        tokens_used=tokens,
                    )
                    session.add(report)

                await session.commit()
                logger.info("trade_explanations_generated", count=len(trade_rows))

        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("generate_trade_explanations_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="anchor.scheduler.jobs.analyze_journal_patterns", bind=True, max_retries=1)
def analyze_journal_patterns(self):
    """
    Weekly AI analysis of the last 90 days of trades to surface performance patterns.

    Runs Sunday 21:00 UTC (just before weekly synthesis at 22:00). Uses Claude Opus
    to identify best/worst pairs, time-of-day patterns, signal quality correlations,
    and regime performance. Saves as IntelligenceReport(JOURNAL_ANALYSIS).
    """
    from anchor.database.engine import get_session as _get_session_ja
    from anchor.database.models import IntelligenceReport
    from anchor.intelligence.trade_intelligence import analyze_journal_patterns as _analyze
    from sqlalchemy import text as _sqlt
    from datetime import datetime, timedelta, timezone as _tz

    async def _inner():
        from anchor.database.engine import init_db as _init_db_ja
        await _init_db_ja()
        cutoff = datetime.now(_tz.utc) - timedelta(days=90)

        async with _get_session_ja() as session:
            rows = (await session.execute(_sqlt("""
                SELECT
                    t.instrument, t.direction,
                    t.session_at_entry, t.regime_at_entry,
                    s.confluence_score,
                    CAST(t.net_pl AS float),
                    COALESCE(CAST(t.net_pl AS float) /
                        NULLIF(CAST(t.entry_price AS float), 0) * 100, 0) AS pl_pct,
                    t.duration_minutes, t.close_reason,
                    t.opened_at, t.closed_at
                FROM trades t
                LEFT JOIN signals s ON s.id = t.signal_id
                WHERE t.closed_at >= :cutoff
                ORDER BY t.closed_at DESC
                LIMIT 500
            """), {"cutoff": cutoff})).fetchall()

            if not rows or len(rows) < 10:
                logger.info("journal_analysis_skipped", reason="insufficient_trades", count=len(rows))
                return

            trades_data = [
                {
                    "instrument":      r[0],
                    "direction":       r[1],
                    "session":         r[2],
                    "regime":          r[3],
                    "confluence_score": float(r[4]) if r[4] else None,
                    "net_pl":          r[5],
                    "pl_pct":          round(r[6], 4),
                    "duration_minutes": r[7],
                    "close_reason":    r[8],
                    "opened_at":       str(r[9]),
                    "closed_at":       str(r[10]),
                }
                for r in rows
            ]

            content, tokens = await _analyze(trades_data)
            if not content:
                return

            report = IntelligenceReport(
                report_type="JOURNAL_ANALYSIS",
                content=content,
                context_snapshot={"trade_count": len(trades_data), "days": 90},
                tokens_used=tokens,
            )
            session.add(report)
            await session.commit()
            logger.info("journal_analysis_saved", trades=len(trades_data), tokens=tokens)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("analyze_journal_patterns_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)
