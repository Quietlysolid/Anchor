"""
LLM-powered brief, debrief, and weekly synthesis generation.

Model strategy (cost vs quality):
  Presession / Postsession — Sonnet 4.6, no thinking (structured template, fast)
  Weekly synthesis         — Sonnet 4.6 + adaptive thinking (cross-week reasoning)
  Journal analysis         — Opus 4.6 + adaptive thinking (deep 90-day pattern mining)
"""
from __future__ import annotations

import json
from typing import Any

import anthropic
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_MODEL_SONNET = "claude-sonnet-4-6"   # presession, postsession, weekly
_MODEL_OPUS   = "claude-opus-4-6"    # journal analysis only

_PRESESSION_SYSTEM = """\
You are the intelligence layer for Anchor, an autonomous algorithmic FX trading system.

Anchor trades 6 currency pairs (EUR_USD, GBP_USD, NZD_USD, USD_CAD, EUR_JPY, AUD_USD) using \
two primary strategies:
- London trend-following (07:00–12:00 UTC) with a 0.72 confluence score threshold
- London Close Reversal (LCR, 17:00–19:00 UTC) betting on mean reversion at NY close

Your role is to synthesize live market context into a structured pre-session brief. \
Be direct, specific, and quantitative. Reference actual numbers from the context. No hedging.

Format your response exactly as:

**MACRO ENVIRONMENT**
[2-3 sentences: what is driving FX today? Risk-on or risk-off? Dominant narrative?]

**SESSION OUTLOOK**
[2-3 sentences: trending or choppy day likely? Why? What should Anchor watch for?]

**KEY RISKS**
• [bullet: events/factors that could suppress signals today]
• [repeat for each risk]

**PAIR FOCUS**
• [INSTRUMENT]: [one line on macro tailwinds/headwinds given rate diff, COT, cross-asset]
• [repeat for each pair worth noting]

**CALENDAR GUIDANCE**
• [PAIR or currency]: [upcoming event + whether to favor, avoid, or stay neutral — one line each]
• [repeat for each pair affected by events in the next 24h; skip pairs with no relevant events]

**CONVICTION**
Today's environment is [TRENDING / CHOPPY / MIXED] because [one-sentence reason].
"""

_POSTSESSION_SYSTEM = """\
You are the intelligence layer for Anchor, an autonomous algorithmic FX trading system.

The London session (07:00–12:00 UTC) has just closed. Your role is to generate a structured \
post-session debrief that analytically connects what happened to why it happened.

Be honest about underperformance. If signals were suppressed or the session was choppy, \
explain the macro cause, not just what the system did.

Format your response exactly as:

**SESSION SUMMARY**
[2-3 sentences: what happened? trades taken, net P&L, signals fired vs suppressed]

**WHY THE SESSION BEHAVED THIS WAY**
[2-3 sentences: connect the macro context — news releases, risk sentiment, rate moves — \
to the actual session behavior]

**SIGNAL PERFORMANCE**
[Bullet breakdown: which pairs fired, what confluence scores, any suppression patterns worth noting]

**WATCH FOR TOMORROW**
• [bullet: key macro theme or event in next 24h]
• [repeat]

**ONE-LINE VERDICT**
[One direct sentence: was today's session quality good/bad/neutral and why]
"""

_WEEKLY_SYSTEM = """\
You are the intelligence layer for Anchor, an autonomous algorithmic FX trading system.

A full trading week has just ended. Your role is to generate a structured weekly synthesis \
that covers performance, macro themes, and preparation for the coming week.

Be analytically honest. Identify what worked, what didn't, and why — connecting both to the \
macro environment the system traded in.

Format your response exactly as:

**WEEK IN REVIEW**
[3-4 sentences: dominant macro themes, overall performance, stand-out pairs]

**PERFORMANCE BREAKDOWN**
[Bullet: per-pair P&L and win rate vs 30-day rolling baseline, any outliers]

**MACRO THEMES THAT MATTERED**
• [bullet: each theme that actually moved the pairs this week]

**EDGE ASSESSMENT**
[2 sentences: is the system trading in alignment with its edge? Any signs of regime change?]

**COMING WEEK SETUP**
• [bullet: major events, central bank decisions, data releases to watch]
• [repeat]

**WEEKLY VERDICT**
[One-sentence summary of the week and what the biggest risk/opportunity is heading into next week]
"""


_SCORE_SYSTEM = """\
You are the risk management layer for Anchor, an autonomous FX trading system.

Read the post-session debrief below and output a single integer from 0 to 10 representing \
the edge quality of the London session just completed.

0  = catastrophic (news-driven chaos, no edge, heavy signal suppression, net loss day)
5  = average (mixed results, some edge captured, some choppy periods)
10 = ideal (clear trending, strong confluence, system working perfectly as designed)

Consider: win rate vs expectation, signal suppression frequency, macro alignment, \
overall system behaviour vs its edge thesis.

Output ONLY a single integer (0–10). Nothing else.
"""

_SCORE_MODEL = "claude-haiku-4-5-20251001"


async def score_postsession(debrief_content: str) -> int:
    """
    Score the postsession debrief 0–10 for edge quality.

    Uses Haiku for speed and cost. Returns 5 (neutral) on any failure.
    Called from jobs._run_intelligence_brief after POSTSESSION generation.
    """
    if not settings.anthropic_api_key:
        return 5

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=_SCORE_MODEL,
            max_tokens=8,
            system=_SCORE_SYSTEM,
            messages=[{"role": "user", "content": debrief_content}],
        )
        text = next(
            (block.text for block in response.content if block.type == "text"),
            "5",
        ).strip()
        score = max(0, min(10, int(text)))
        logger.info("postsession_scored", score=score)
        return score
    except Exception as exc:
        logger.warning("postsession_score_failed", error=str(exc))
        return 5


async def generate_presession_brief(context: dict[str, Any]) -> tuple[str, int]:
    """Generate London pre-session brief. Returns (content, tokens_used)."""
    return await _generate_fast("PRESESSION", _PRESESSION_SYSTEM, context)


async def generate_postsession_debrief(context: dict[str, Any]) -> tuple[str, int]:
    """Generate post-London-session debrief. Returns (content, tokens_used)."""
    return await _generate_fast("POSTSESSION", _POSTSESSION_SYSTEM, context)


async def generate_weekly_synthesis(context: dict[str, Any]) -> tuple[str, int]:
    """Generate weekly synthesis. Returns (content, tokens_used)."""
    return await _generate_deep("WEEKLY", _WEEKLY_SYSTEM, context, model=_MODEL_SONNET)


async def _generate_fast(report_type: str, system_prompt: str, context: dict[str, Any]) -> tuple[str, int]:
    """Sonnet 4.6, no extended thinking — for daily structured briefs."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    context_str = json.dumps(context, indent=2, default=str)
    user_message = f"Live system context as of {context.get('generated_at_utc', 'now')}:\n\n{context_str}"

    logger.info("intelligence_generating", report_type=report_type, model=_MODEL_SONNET, context_chars=len(context_str))

    response = await client.messages.create(
        model=_MODEL_SONNET,
        max_tokens=1200,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    content = next((b.text for b in response.content if b.type == "text"), "")
    tokens  = response.usage.input_tokens + response.usage.output_tokens
    logger.info("intelligence_generated", report_type=report_type, tokens=tokens, chars=len(content))
    return content, tokens


async def _generate_deep(
    report_type: str,
    system_prompt: str,
    context: dict[str, Any],
    model: str = _MODEL_OPUS,
) -> tuple[str, int]:
    """Streaming with adaptive thinking — for weekly/journal deep analysis."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    context_str = json.dumps(context, indent=2, default=str)
    user_message = f"Live system context as of {context.get('generated_at_utc', 'now')}:\n\n{context_str}"

    logger.info("intelligence_generating", report_type=report_type, model=model, context_chars=len(context_str))

    async with client.messages.stream(
        model=model,
        max_tokens=1200,
        thinking={"type": "adaptive"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        final = await stream.get_final_message()

    content = next((b.text for b in final.content if b.type == "text"), "")
    tokens  = final.usage.input_tokens + final.usage.output_tokens
    logger.info("intelligence_generated", report_type=report_type, tokens=tokens, chars=len(content))
    return content, tokens
