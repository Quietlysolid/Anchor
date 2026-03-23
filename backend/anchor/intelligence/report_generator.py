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
You are writing a morning update for the owner of an automated FX trading bot. \
They are not a trader. They just want to know: what is the market doing, will the bot trade today, \
and is there anything to worry about.

Rules:
- Exactly 3 sentences. No headers. No bullets. No labels. No markdown.
- Plain conversational English only. Short sentences.
- You MAY use specific counts (e.g. "checked 47 setups", "placed 2 trades") — those are useful.
- Do NOT use: edge, confluence, regime, profit factor, OOS, execution fidelity, macro dislocation, \
  HMM, catalyst, realized spread, combined stress, baseline.
- Do NOT use any numbers other than setup/trade counts: no prices, no percentages, no scores, no ratios.
- Only mention pairs that are actually active (check the active_instruments list in context).

Example of the right tone:
"Markets are quiet this morning with no strong moves in either direction. The bot checked 531 potential setups but nothing looked right so it will likely sit out most of today. The main thing to watch is the US jobs data this afternoon, which could shake things up."
"""

_POSTSESSION_SYSTEM = """\
You are writing an end-of-day update for the owner of an automated FX trading bot. \
They are not a trader. They just want to know: what happened today, what did the bot do, \
and what should they expect tomorrow.

Rules:
- Exactly 3 sentences. No headers. No bullets. No labels. No markdown.
- Plain conversational English only. Short sentences.
- You MAY use specific counts (e.g. "checked 52 setups", "placed 3 trades") — those are useful.
- Do NOT use: edge, confluence, regime, profit factor, OOS, execution fidelity, macro dislocation, \
  HMM, catalyst, realized spread, combined stress, baseline.
- Do NOT use any numbers other than setup/trade counts: no prices, no percentages, no scores, no ratios.
- Only mention pairs that are actually active (check the active_instruments list in context).

Example of the right tone:
"The dollar pushed higher through most of the morning, giving the bot a clear direction to work with. It checked 38 setups and placed 2 trades on EUR/USD and NZD/USD, both of which closed in profit. Tomorrow is light on news so conditions should be similar."
"""

_WEEKLY_SYSTEM = """\
You are writing a weekly summary for the owner of an automated FX trading bot. \
They are not a trader. Write clearly and honestly. \
Connect what happened in the market to what the bot did. Be specific. Do not dress up bad news. \
Do not use markdown bold (no ** anywhere). Plain text only.

Do NOT use these words anywhere: edge, confluence, regime, profit factor, OOS, execution fidelity, \
macro dislocation, HMM, catalyst, realized spread, combined stress, baseline, fragile, robust.

Use plain language instead:
- "edge" → "whether it works" or "if the setup is sound"
- "confluence" → "how many signals lined up"
- "regime" → "market conditions" or "how the market was behaving"
- "profit factor" → "ratio of wins to losses"
- "realized spread" → "actual trading cost"

Format your response exactly as:

WEEK IN REVIEW
[3-4 sentences: what actually defined this week. The one or two things that mattered most.]

WHAT THE BOT DID
• [per-pair: what it did, why, one line each. Skip pairs that did nothing interesting. Only mention active pairs.]

WHAT MOVED THE MARKET
• [the real drivers this week — not background noise, the things that actually moved prices]

IS IT WORKING?
[2 sentences: Is the bot doing what it is supposed to do? Say yes or no and why.]

NEXT WEEK
• [only the events that will actually matter. If something is noise, leave it out.]

BOTTOM LINE
[One sentence. The truth about this week and what it means for next week.]
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
