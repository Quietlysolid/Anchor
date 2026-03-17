"""
Session quality gate — structured environment scoring alongside the presession brief.

Called from jobs._run_intelligence_brief (PRESESSION only).
Result is written to Redis key 'session_quality' with TTL 8 hours.

Signal engine reads this key to:
  - adjust confluence threshold (TRENDING: -0.02, MIXED: 0.00, CHOPPY: +0.06)
  - skip low-ranked pairs on CHOPPY days with high confidence (Tier 2a)

Position sizer reads size_scale to reduce position size on CHOPPY/MIXED days.

Fallback: if Redis key missing or Claude fails, all callers use safe defaults
(MIXED environment, full size, zero threshold adjustment).
"""
from __future__ import annotations

import json
from typing import Any

import anthropic
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_MODEL = "claude-sonnet-4-6"   # Sonnet for latency; this is a structured task not a narrative one
_REDIS_KEY = "session_quality"
_TTL_SECONDS = 28_800           # 8 hours

_ACTIVE_PAIRS = settings.instruments  # derived from config — edit config.py to change

_SYSTEM = """\
You are the risk management layer for Anchor, an autonomous FX trading system.

Based on the provided market context, output a single JSON object — no markdown, \
no explanation, no code fences. Score today's London session trading environment.

The JSON must contain exactly these fields:
{
  "environment": "TRENDING" | "CHOPPY" | "MIXED",
  "confidence": <float 0.0–1.0, how certain you are of this assessment>,
  "size_scale": <float 0.5–1.0, position-size multiplier>,
  "threshold_adjustment": <float -0.04 to +0.06, added to the base threshold of 0.72>,
  "pair_rankings": ["PAIR1", "PAIR2", "PAIR3", "PAIR4", "PAIR5", "PAIR6"]
}

Environment rules:
- TRENDING: clear directional momentum, aligned COT + rate differentials, VIX ≤ 18, \
no major scheduled event risk in the London window
  → suggested threshold_adjustment: -0.02, size_scale: 1.0
- MIXED: some trend pockets but unclear overall environment, moderate VIX (15–22), \
one or two conflicting macro signals
  → suggested threshold_adjustment: 0.0, size_scale: 0.85
- CHOPPY: high VIX (>22), contradictory macro signals, news-driven session, or ranging day \
with significant scheduled event risk inside the London window
  → suggested threshold_adjustment: +0.06, size_scale: 0.65

pair_rankings: rank all 6 active pairs from strongest macro tailwind to weakest, \
using rate differentials, COT positioning, cross-asset risk sentiment, and economic surprise data.
Active pairs: EUR_USD, GBP_USD, NZD_USD, USD_CAD, EUR_JPY, AUD_USD

Output ONLY the JSON object. Nothing else.
"""

# Safe defaults — returned whenever Claude is unavailable or the response is unparseable.
# MIXED + no size change + no threshold change = no behavioural impact.
DEFAULT: dict[str, Any] = {
    "environment": "MIXED",
    "confidence": 0.5,
    "size_scale": 1.0,
    "threshold_adjustment": 0.0,
    "pair_rankings": list(_ACTIVE_PAIRS),
}


async def generate_session_quality(context: dict[str, Any]) -> dict[str, Any]:
    """
    Call Claude Sonnet to score today's session environment.

    Returns a validated dict ready for json.dumps() and Redis storage.
    Falls back to DEFAULT on any error (fail-open: MIXED, full size, no threshold shift).
    """
    if not settings.anthropic_api_key:
        logger.warning("session_quality_skipped_no_api_key")
        return dict(DEFAULT)

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    context_str = json.dumps(context, indent=2, default=str)

    try:
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=256,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Market context:\n\n{context_str}"}],
        )

        text = next(
            (block.text for block in response.content if block.type == "text"),
            "",
        ).strip()

        raw = json.loads(text)

        env = raw.get("environment", "MIXED")
        if env not in ("TRENDING", "CHOPPY", "MIXED"):
            env = "MIXED"

        rankings: list[str] = raw.get("pair_rankings", list(_ACTIVE_PAIRS))
        # Ensure every active pair is present; append any missing ones at the end
        for p in _ACTIVE_PAIRS:
            if p not in rankings:
                rankings.append(p)

        result: dict[str, Any] = {
            "environment": env,
            "confidence": max(0.0, min(1.0, float(raw.get("confidence", 0.5)))),
            "size_scale": max(0.5, min(1.0, float(raw.get("size_scale", 1.0)))),
            "threshold_adjustment": max(-0.04, min(0.06, float(raw.get("threshold_adjustment", 0.0)))),
            "pair_rankings": rankings,
        }

        logger.info(
            "session_quality_generated",
            environment=result["environment"],
            confidence=result["confidence"],
            size_scale=result["size_scale"],
            threshold_adj=result["threshold_adjustment"],
            top3=rankings[:3],
        )
        return result

    except Exception as exc:
        logger.warning("session_quality_failed", error=str(exc))
        return dict(DEFAULT)


async def get_session_quality(redis_client) -> dict[str, Any]:
    """
    Read session quality from Redis.

    Returns DEFAULT if the key is missing or unparseable.
    This is the read-path used by engine.py and the scan task.
    """
    try:
        raw = await redis_client.get(_REDIS_KEY)
        if raw:
            data = json.loads(raw)
            # Ensure required keys exist before returning
            if "environment" in data and "size_scale" in data:
                return data
    except Exception as exc:
        logger.warning("session_quality_redis_read_failed", error=str(exc))
    return dict(DEFAULT)
