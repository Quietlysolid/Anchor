"""
Trade-level and portfolio-level AI intelligence for Anchor.

Functions (all fail-open — no error propagates to caller):

  explain_trade()            — per-trade win/loss explanation (Haiku)
  narrate_drawdown()         — diagnose drawdown cause + recommend action (Haiku)
  interpret_cot()            — raw COT numbers → directional bias per currency (Haiku)
  diagnose_parameter_drift() — live WR/PF vs benchmarks → threshold recommendation (Haiku)
  analyze_journal_patterns() — monthly pattern mining across all trades (Opus)
"""
from __future__ import annotations

import json
from typing import Any

import anthropic
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_MODEL_HAIKU = "claude-haiku-4-5-20251001"
_MODEL_OPUS  = "claude-opus-4-6"


# ── 1. Trade-level explanation ────────────────────────────────────────────────

_TRADE_EXPLAIN_SYSTEM = """\
You are the post-trade analyst for Anchor, an autonomous FX trading system.

Review a single closed trade and explain in 2-3 sentences why it won or lost.
Connect the outcome to the macro environment at entry, the signal quality
(confluence score and sub-scores), the session regime, and the close reason.

Be specific and honest. If the signal was weak or the macro was against the trade,
say so. Reference actual numbers. Do not hedge or pad.

Output ONLY the explanation text. No labels, no headings, no JSON.
"""


async def explain_trade(
    trade_data: dict[str, Any],
    signal_data: dict[str, Any] | None,
    macro_snapshot: dict[str, Any],
) -> tuple[str, int]:
    """
    Generate a natural-language explanation for a single closed trade.

    trade_data:    keys from Trade model (instrument, direction, net_pl, pl_pct,
                   duration_minutes, close_reason, regime_at_entry, session_at_entry,
                   entry_price, exit_price)
    signal_data:   linked Signal row (confluence_score, sub-scores, signal_metadata)
                   or None if signal is unavailable
    macro_snapshot: VIX, rate_differentials, COT, cross-asset from Redis

    Returns (explanation_text, tokens_used). Returns ("", 0) on failure.
    """
    if not settings.anthropic_api_key:
        return "", 0

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    payload = {
        "trade":  trade_data,
        "signal": signal_data or {},
        "macro":  macro_snapshot,
    }
    try:
        response = await client.messages.create(
            model=_MODEL_HAIKU,
            max_tokens=256,
            system=_TRADE_EXPLAIN_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        )
        text   = next((b.text for b in response.content if b.type == "text"), "").strip()
        tokens = response.usage.input_tokens + response.usage.output_tokens
        logger.info("trade_explained", instrument=trade_data.get("instrument"), tokens=tokens)
        return text, tokens
    except Exception as exc:
        logger.warning("trade_explain_failed", error=str(exc))
        return "", 0


# ── 2. Drawdown narration ─────────────────────────────────────────────────────

_DRAWDOWN_NARRATE_SYSTEM = """\
You are the risk diagnostics layer for Anchor, an autonomous FX trading system.

A drawdown circuit breaker has just triggered. Review the recent trade history
and macro backdrop to diagnose WHY this drawdown is occurring and what to do.

Possible causes to investigate:
- Regime shift: are multiple pairs trending against the system's long-term bias?
- Correlated losses: are the losses concentrated in correlated pairs (e.g. EUR+GBP both short)?
- News-driven: did a major data release or central bank event cause abnormal moves?
- Strategy drift: is one strategy (London trend vs LCR) underperforming disproportionately?
- Macro dislocation: is price action broadly contradicting fundamental expectations?

Format your response exactly as:

**DIAGNOSIS**
[2-3 sentences: what is causing this drawdown based on the data?]

**CONCENTRATED IN**
[One sentence: which pairs/sessions/strategies are driving the losses?]

**RECOMMENDED ACTION**
[One concrete sentence: beyond size reduction — should we pause a specific pair,
wait for regime clarification, or hold course?]
"""


async def narrate_drawdown(
    trigger_type: str,
    drawdown_pct: float,
    recent_trades: list[dict[str, Any]],
    macro_snapshot: dict[str, Any],
) -> tuple[str, int]:
    """
    Diagnose an active drawdown and recommend a response.

    trigger_type:  "REDUCE" (8% threshold) or "HALT" (15% threshold)
    drawdown_pct:  current drawdown as a decimal (e.g. 0.09 for 9%)
    recent_trades: last 20 closed trades (instrument, direction, net_pl, session,
                   regime_at_entry, close_reason, closed_at)
    macro_snapshot: VIX, cross_asset, rate_differentials from Redis

    Returns (narration_text, tokens_used). Returns ("", 0) on failure.
    """
    if not settings.anthropic_api_key:
        return "", 0

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    payload = {
        "trigger_type":   trigger_type,
        "drawdown_pct":   f"{drawdown_pct:.2%}",
        "recent_trades":  recent_trades,
        "macro":          macro_snapshot,
    }
    try:
        response = await client.messages.create(
            model=_MODEL_HAIKU,
            max_tokens=400,
            system=_DRAWDOWN_NARRATE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        )
        text   = next((b.text for b in response.content if b.type == "text"), "").strip()
        tokens = response.usage.input_tokens + response.usage.output_tokens
        logger.info("drawdown_narrated", trigger=trigger_type, dd_pct=f"{drawdown_pct:.2%}", tokens=tokens)
        return text, tokens
    except Exception as exc:
        logger.warning("drawdown_narrate_failed", error=str(exc))
        return "", 0


# ── 3. COT interpretation ─────────────────────────────────────────────────────

_COT_INTERPRET_SYSTEM = """\
You are the COT (Commitments of Traders) analyst for Anchor, an autonomous FX system.

You receive CFTC Disaggregated Futures data for major currencies:
- net_noncommercial: Leveraged Money (speculators) net position (positive = net long)
- net_commercial: Dealer net position (positive = net long)

Interpret each currency's institutional positioning into a directional bias:
BULLISH, BEARISH, or NEUTRAL — with a one-sentence explanation referencing the
actual numbers.

Rules:
- Large positive net_noncommercial (>20,000) = speculators heavily long = momentum BULLISH
- Large negative net_noncommercial (<-20,000) = speculators heavily short = momentum BEARISH
- Extreme readings (>50,000 or <-50,000) increase conviction
- Commercial positioning (dealers) is secondary — note only when it diverges strongly
- When net_noncommercial is -10,000 to +10,000, call it NEUTRAL

Output a JSON object only:
{
  "USD":  {"bias": "BULLISH|BEARISH|NEUTRAL", "reason": "one sentence"},
  "EUR":  {"bias": "...", "reason": "..."},
  ...
}
Include only the currencies present in the input. Output ONLY the JSON. No markdown.
"""


async def interpret_cot(cot_data: dict[str, dict]) -> tuple[dict[str, dict], int]:
    """
    Translate raw COT numbers into directional bias per currency.

    cot_data: {currency: {net_noncommercial, net_commercial, report_date}}

    Returns ({currency: {bias, reason}}, tokens_used).
    Returns ({}, 0) on failure.
    """
    if not settings.anthropic_api_key or not cot_data:
        return {}, 0

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=_MODEL_HAIKU,
            max_tokens=512,
            system=_COT_INTERPRET_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(cot_data, default=str)}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "{}").strip()
        tokens = response.usage.input_tokens + response.usage.output_tokens
        interpreted = json.loads(text)
        logger.info("cot_interpreted", currencies=list(interpreted.keys()), tokens=tokens)
        return interpreted, tokens
    except Exception as exc:
        logger.warning("cot_interpret_failed", error=str(exc))
        return {}, 0


# ── 4. Parameter drift diagnosis ──────────────────────────────────────────────

_DRIFT_DIAGNOSE_SYSTEM = """\
You are the performance analyst for Anchor, an autonomous FX trading system.

A live performance alert has fired: actual trading results are drifting below
backtested benchmarks. Review the live statistics and recent trade breakdown
to diagnose why and recommend a specific corrective action.

Possible causes:
- Regime mismatch: the strategy was backtested on trending markets; current markets are choppy
- Specific pair underperformance: one or two pairs are dragging down the average
- Session timing: LCR or London trend is underperforming disproportionately
- Threshold too low: signals at lower confluence are losing more than expected

Format your response exactly as:

**DRIFT DIAGNOSIS**
[2 sentences: what is causing the underperformance?]

**UNDERPERFORMING PAIR/SESSION**
[One sentence: which pair or strategy session is the primary drag?]

**RECOMMENDED ADJUSTMENT**
[One specific, actionable sentence: e.g., "Raise threshold for EUR_JPY to 0.76 until
win rate recovers" or "Pause LCR on GBP_USD until regime normalises"]
"""


async def diagnose_parameter_drift(
    strategy_label: str,
    live_stats: dict[str, Any],
    benchmarks: dict[str, Any],
    recent_trades: list[dict[str, Any]],
) -> tuple[str, int]:
    """
    Diagnose why live WR/PF is drifting below backtest benchmarks.

    strategy_label: "LONDON" or "LCR"
    live_stats:     {rolling_wr_pct, rolling_pf, n_trades, days_since_win, per_pair}
    benchmarks:     {wr, pf} backtest expectations
    recent_trades:  last 30 trades for this strategy

    Returns (diagnosis_text, tokens_used). Returns ("", 0) on failure.
    """
    if not settings.anthropic_api_key:
        return "", 0

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    payload = {
        "strategy":      strategy_label,
        "live_stats":    live_stats,
        "benchmarks":    benchmarks,
        "recent_trades": recent_trades,
    }
    try:
        response = await client.messages.create(
            model=_MODEL_HAIKU,
            max_tokens=400,
            system=_DRIFT_DIAGNOSE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        )
        text   = next((b.text for b in response.content if b.type == "text"), "").strip()
        tokens = response.usage.input_tokens + response.usage.output_tokens
        logger.info("parameter_drift_diagnosed", strategy=strategy_label, tokens=tokens)
        return text, tokens
    except Exception as exc:
        logger.warning("parameter_drift_diagnose_failed", strategy=strategy_label, error=str(exc))
        return "", 0


# ── 5. Journal pattern analysis ───────────────────────────────────────────────

_JOURNAL_ANALYZE_SYSTEM = """\
You are the trading pattern analyst for Anchor, an autonomous FX trading system.

Review the last 90 days of closed trades and identify statistically meaningful
patterns in the system's performance. Look for:

1. Best and worst performing pairs by win rate and profit factor
2. Time-of-day patterns: do early London trades (07–09 UTC) outperform late (10–12)?
3. Signal quality correlation: do higher confluence scores actually produce better outcomes?
4. Regime correlation: does the system perform better in TRENDING vs RANGING regimes?
5. Session comparison: London trend vs LCR — which is contributing more profit?
6. Duration analysis: are quick wins or held positions more profitable?
7. Close reason breakdown: SL hits vs TP hits vs partial — what's the pattern?

Be specific and quantitative. Reference actual numbers from the trade data.
Only report patterns with clear evidence — no speculation.

Format your response exactly as:

**EDGE PATTERNS**
• [bullet: strongest statistical pattern found with numbers]
• [repeat for each meaningful pattern, max 5]

**UNDERPERFORMING SEGMENTS**
• [bullet: pair, session, or regime that is dragging results — with data]

**SYSTEM HEALTH**
[2-3 sentences: is the system trading in alignment with its edge?
Are there signs of regime deterioration or signal decay?]

**RECOMMENDED FOCUS**
[One specific actionable insight for the operator — threshold, pair, or timing adjustment]
"""


async def analyze_journal_patterns(trades_data: list[dict[str, Any]]) -> tuple[str, int]:
    """
    Mine patterns across the last 90 days of closed trades.

    trades_data: list of trade dicts — instrument, direction, session_at_entry,
                 regime_at_entry, confluence_score, net_pl, pl_pct, duration_minutes,
                 close_reason, opened_at, closed_at

    Returns (analysis_text, tokens_used). Returns ("", 0) on failure.
    """
    if not settings.anthropic_api_key or not trades_data:
        return "", 0

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    payload = {"trades": trades_data, "trade_count": len(trades_data)}
    try:
        async with client.messages.stream(
            model=_MODEL_OPUS,
            max_tokens=1200,
            thinking={"type": "adaptive"},
            system=_JOURNAL_ANALYZE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        ) as stream:
            final = await stream.get_final_message()

        text   = next((b.text for b in final.content if b.type == "text"), "").strip()
        tokens = final.usage.input_tokens + final.usage.output_tokens
        logger.info("journal_patterns_analyzed", n_trades=len(trades_data), tokens=tokens)
        return text, tokens
    except Exception as exc:
        logger.warning("journal_analyze_failed", error=str(exc))
        return "", 0
