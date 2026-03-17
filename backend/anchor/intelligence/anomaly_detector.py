"""
Macro anomaly detector — post-session price vs macro dissonance detection.

Called after each postsession debrief. Asks Claude Haiku to identify pairs
where today's price action contradicted the macro setup (rate differentials,
COT positioning, cross-asset risk sentiment).

Writes Redis keys:
  macro_dissonance:{PAIR}  →  reason string  (TTL: 20 hours)

Signal engine reads these keys and applies a -0.05 confluence penalty on any
flagged pair until the key naturally expires (i.e. for the remainder of the
current day and the next full London + LCR session).

Fail-open: on any error, no keys are written and engine behaviour is unchanged.
"""
from __future__ import annotations

import json
from typing import Any

import anthropic
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_MODEL = "claude-haiku-4-5-20251001"
_TTL_SECONDS = 72_000   # 20 hours — covers remainder of day + next London + LCR sessions

_ACTIVE_PAIRS = ["EUR_USD", "GBP_USD", "NZD_USD", "USD_CAD", "EUR_JPY", "AUD_USD"]

_SYSTEM = """\
You are the risk management layer for Anchor, an autonomous FX trading system.

Review the postsession context and identify pairs where today's London session \
price action showed clear dissonance with their underlying macro setup.

Dissonance means: price moved meaningfully against what rate differentials, \
COT positioning, or cross-asset risk sentiment would predict for the session.

Examples of dissonance:
- EUR/USD rallied strongly despite USD having higher rates and hawkish Fed narrative
- AUD/USD fell sharply on a risk-on day where SPY was up and gold was stable
- USD/CAD rose despite oil rising (CAD typically benefits from higher oil)
- GBP/USD dropped despite risk-on flows and positive UK data surprises

Output a single JSON object — no markdown, no explanation, no code fences:
{
  "dissonance_pairs": ["PAIR1", "PAIR2"],
  "assessments": {
    "PAIR1": "one-sentence explanation of the dissonance",
    "PAIR2": "one-sentence explanation"
  }
}

Rules:
- Only flag pairs with clear, significant dissonance — not minor noise or small retracements
- If no dissonance detected, return: {"dissonance_pairs": [], "assessments": {}}
- Only use pairs from this list: EUR_USD, GBP_USD, NZD_USD, USD_CAD, EUR_JPY, AUD_USD
- Output ONLY the JSON object. Nothing else.
"""


async def detect_macro_anomaly(
    context: dict[str, Any],
    redis_client,
) -> list[str]:
    """
    Detect price vs macro dissonance for the session just completed.

    Writes  macro_dissonance:{pair}  =  reason_string  to Redis for flagged pairs.
    Clears the key for any pair that is no longer flagged.
    Returns list of dissonant pair names (may be empty).

    Fails open — on any error returns [] and leaves Redis unchanged.
    """
    if not settings.anthropic_api_key:
        return []

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    context_str = json.dumps(context, indent=2, default=str)

    try:
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Postsession context:\n\n{context_str}"}],
        )
        text = next(
            (block.text for block in response.content if block.type == "text"),
            "{}",
        ).strip()

        raw = json.loads(text)
        dissonant: list[str] = [
            p for p in raw.get("dissonance_pairs", [])
            if p in _ACTIVE_PAIRS
        ]
        assessments: dict[str, str] = raw.get("assessments", {})

        # Write flags for dissonant pairs
        for pair in dissonant:
            key = f"macro_dissonance:{pair}"
            reason = str(assessments.get(pair, "price_vs_macro_dissonance"))[:200]
            await redis_client.set(key, reason, ex=_TTL_SECONDS)
            logger.info("macro_dissonance_flagged", pair=pair, reason=reason)

        # Clear stale flags for pairs no longer dissonant
        for pair in _ACTIVE_PAIRS:
            if pair not in dissonant:
                await redis_client.delete(f"macro_dissonance:{pair}")

        logger.info(
            "macro_anomaly_scan_complete",
            dissonant_pairs=dissonant,
            n=len(dissonant),
        )
        return dissonant

    except Exception as exc:
        logger.warning("macro_anomaly_detection_failed", error=str(exc))
        return []
