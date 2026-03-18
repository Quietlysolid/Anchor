"""
LLM-powered brief, debrief, and weekly synthesis generation.

Model strategy (cost vs quality):
  Presession / Postsession — Sonnet 4.6, no thinking (structured template, fast)
  Weekly synthesis         — Sonnet 4.6 + adaptive thinking (cross-week reasoning)
  Journal analysis         — Opus 4.6 + adaptive thinking (deep 90-day pattern mining)
"""
from __future__ import annotations

import asyncio
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

Anchor trades 6 pairs (EUR_USD, GBP_USD, NZD_USD, USD_CAD, EUR_JPY, AUD_USD) using London \
trend-following (07–12 UTC, threshold 0.72) and London Close Reversal (17–19 UTC).

Write the pre-session brief. 150 words max. Write like Steve Jobs thinks: \
short declarative sentences that cut to the truth. No hedging. No "may" or "could". \
State what the market is doing, not what it might do. \
No markdown bold (no ** anywhere). Plain text only.

Format exactly as:

MACRO ENVIRONMENT
[1-2 sentences: name the dominant force. State it as a fact, not a possibility.]

SESSION OUTLOOK
[1-2 sentences: trending or choppy. Say which one. Say why. No equivocation.]

KEY RISKS
• [one line — 2 bullets max. Name the actual risk. Not "volatility". The specific thing.]

PAIR FOCUS
• [INSTRUMENT]: [one line — tailwind or headwind. Only pairs where the case is clear.]

CALENDAR GUIDANCE
• [currency/event]: [favor / avoid / neutral — one line. Skip anything without a real read.]

CONVICTION
[TRENDING / CHOPPY / MIXED] — [one clause. The reason, stated plainly].
"""

_POSTSESSION_SYSTEM = """\
You are the intelligence layer for Anchor, an autonomous algorithmic FX trading system.

The London session just closed. Write the debrief. 120 words max. \
Write the way Steve Jobs would debrief a product launch: honest, direct, no spin. \
If it was good, say it was good and why. If it was bad, say it was bad and own it. \
No jargon. No raw numbers. No hedging. \
No markdown bold (no ** anywhere). Plain text only.

Format exactly as:

SESSION SUMMARY
[1-2 sentences: what actually happened. State it plainly. Not "mixed conditions" — say what moved and where.]

WHY IT HAPPENED
[1-2 sentences: the real reason. One cause, stated with confidence.]

PAIRS TO WATCH TOMORROW
• [pair or event]: [one line — 2 bullets max. Only things that actually matter.]

VERDICT
[One sentence. Honest. If the system did the right thing, say so. If something is concerning, say that.]
"""

_WEEKLY_SYSTEM = """\
You are the intelligence layer for Anchor, an autonomous algorithmic FX trading system.

A full trading week has ended. Write the weekly synthesis. \
Write the way Steve Jobs would do an annual review: zoom out, find the truth, \
say it clearly, don't dress up bad news and don't undersell good news. \
Connect what happened in the market to what the system did. Be specific. Be honest. \
Do not use markdown bold (no ** anywhere). Plain text only.

Format your response exactly as:

WEEK IN REVIEW
[3-4 sentences: what actually defined this week. The one or two forces that mattered above everything else.]

PERFORMANCE BREAKDOWN
• [per-pair: what it did, why, one line each. Skip the pairs that did nothing interesting.]

MACRO THEMES THAT MATTERED
• [each theme that genuinely moved prices this week — not background noise, the real drivers]

EDGE ASSESSMENT
[2 sentences: Is the system capturing its edge or fighting the market? Say which one. Say why.]

COMING WEEK SETUP
• [only the events that will actually matter. If it is noise, leave it out.]

WEEKLY VERDICT
[One sentence. The truth about this week and what it means for next week. Make it count.]
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

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.messages.create(
                model=_MODEL_SONNET,
                max_tokens=600,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            content = next((b.text for b in response.content if b.type == "text"), "")
            tokens  = response.usage.input_tokens + response.usage.output_tokens
            logger.info("intelligence_generated", report_type=report_type, tokens=tokens, chars=len(content))
            return content, tokens
        except Exception as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("intelligence_api_retry", report_type=report_type, attempt=attempt + 1, wait=wait, error=str(exc))
            await asyncio.sleep(wait)

    raise RuntimeError(f"Claude API failed after 3 attempts: {last_exc}") from last_exc


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

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
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
        except Exception as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("intelligence_api_retry", report_type=report_type, attempt=attempt + 1, wait=wait, error=str(exc))
            await asyncio.sleep(wait)

    raise RuntimeError(f"Claude API failed after 3 attempts: {last_exc}") from last_exc
