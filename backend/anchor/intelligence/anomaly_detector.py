"""
Macro anomaly detector — post-session and intrabar price vs macro dissonance.

Two entry points:

  detect_macro_anomaly()   — called postsession (12:30 UTC). Reviews full session
                             context. TTL 20 hours (covers remainder of day +
                             next full London + LCR sessions). Clears stale flags
                             for non-dissonant pairs.

  detect_intrabar_anomaly() — called hourly during London (07:05–11:05 UTC).
                              Reviews last 4 H1 bars + macro snapshot. TTL 2 hours
                              (expires before next hourly check). Only SETS flags —
                              postsession check owns clearing stale flags.

Both write Redis keys:
  macro_dissonance:{PAIR}  →  reason string

Signal engine reads these keys and applies a -0.05 confluence penalty on any
flagged pair until the key naturally expires.

Fail-open: on any error, no keys are written and engine behaviour is unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import anthropic
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_MODEL = "claude-haiku-4-5-20251001"
_TTL_SECONDS = 72_000        # 20 hours — postsession: covers remainder of day + next London + LCR
_INTRABAR_TTL = 7_200        # 2 hours — intrabar: expires before next hourly check

_ACTIVE_PAIRS = settings.instruments  # derived from config — edit config.py to change

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


# ── Intrabar check ────────────────────────────────────────────────────────────

_INTRABAR_SYSTEM = """\
You are the intrabar risk filter for Anchor, an autonomous FX trading system.

You receive the last 4 H1 candles for each active currency pair plus a snapshot
of the current macro backdrop (rate differentials, COT positioning, cross-asset
sentiment, VIX level, economic surprise scores).

Your job: identify pairs where the current London session price action is clearly
DISSONANT with the underlying macro setup — meaning price is moving in a direction
that strongly contradicts what macro fundamentals would predict.

Dissonance examples:
- EUR/USD rallying strongly despite USD having higher rates and hawkish Fed tone
- AUD/USD falling on a risk-on day (VIX falling, SPY rising, gold stable)
- USD/CAD rising despite oil rising (CAD typically benefits from higher oil prices)
- GBP/USD dropping despite positive UK economic surprises and risk-on cross-asset

Only flag pairs with CLEAR, SUSTAINED dissonance across multiple bars — not brief
retracements, single-candle noise, or normal consolidation.

Output a single JSON object — no markdown, no explanation, no code fences:
{
  "dissonance_pairs": ["PAIR1", "PAIR2"],
  "assessments": {
    "PAIR1": "one-sentence explanation of the intrabar dissonance",
    "PAIR2": "one-sentence explanation"
  }
}

Rules:
- Only flag with high conviction — false flags cost more than missed opportunities
- If no clear dissonance, return: {"dissonance_pairs": [], "assessments": {}}
- Only use pairs from: EUR_USD, GBP_USD, NZD_USD, USD_CAD, EUR_JPY, AUD_USD
- Output ONLY the JSON object. Nothing else.
"""


async def detect_intrabar_anomaly(
    pairs_bars: dict[str, list[dict]],
    macro_snapshot: dict[str, Any],
    redis_client,
) -> list[str]:
    """
    Detect intrabar price vs macro dissonance during the London session.

    pairs_bars:     pair name → list of last 4 H1 OHLC dicts
                    (keys: time, open, high, low, close)
    macro_snapshot: dict with vix, rate_differentials, cot_positioning,
                    cross_asset, economic_surprise from Redis
    redis_client:   async Redis client

    Writes macro_dissonance:{pair} = reason string (TTL 2 hours) for flagged pairs.
    Does NOT clear existing flags — postsession check owns clearing stale flags.
    Returns list of flagged pair names (may be empty).
    Fails open on any error.
    """
    if not settings.anthropic_api_key:
        return []

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    payload = {
        "intrabar_bars": pairs_bars,
        "macro_context": macro_snapshot,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    content_str = json.dumps(payload, indent=2, default=str)

    try:
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=_INTRABAR_SYSTEM,
            messages=[{"role": "user", "content": f"Current London session data:\n\n{content_str}"}],
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

        for pair in dissonant:
            key = f"macro_dissonance:{pair}"
            reason = str(assessments.get(pair, "intrabar_price_vs_macro_dissonance"))[:200]
            await redis_client.set(key, reason, ex=_INTRABAR_TTL)
            logger.info("intrabar_dissonance_flagged", pair=pair, reason=reason)

        logger.info(
            "intrabar_anomaly_scan_complete",
            dissonant_pairs=dissonant,
            n=len(dissonant),
            hour_utc=datetime.now(timezone.utc).hour,
        )
        return dissonant

    except Exception as exc:
        logger.warning("intrabar_anomaly_detection_failed", error=str(exc))
        return []
