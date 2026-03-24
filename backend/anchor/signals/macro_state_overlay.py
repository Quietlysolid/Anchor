"""Slow-moving macro state overlay.

Transforms existing macro caches into a directional prior for each pair:
  - rate differential level
  - rate differential velocity
  - recent economic surprise balance
  - cross-asset risk regime

Used as a confidence modifier / size reducer, not a raw entry engine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from anchor.data.cross_asset import _RISK_SENSITIVITY
from anchor.signals.economic_surprise import _PAIR_CURRENCIES

logger = structlog.get_logger(__name__)


@dataclass
class MacroStateOverlay:
    multiplier: float = 1.0
    veto: bool = False
    regime: str = "NEUTRAL"
    directional_bias: str = "NEUTRAL"
    score: float = 0.0
    detail: dict | None = None

    def to_metadata(self) -> dict:
        return {
            "multiplier": round(self.multiplier, 4),
            "veto": self.veto,
            "regime": self.regime,
            "directional_bias": self.directional_bias,
            "score": round(self.score, 4),
            **(self.detail or {}),
        }


def _bias_from_score(score: float, threshold: float = 0.15) -> str:
    if score >= threshold:
        return "LONG"
    if score <= -threshold:
        return "SHORT"
    return "NEUTRAL"


async def get_macro_state_overlay(
    redis_client,
    instrument: str,
    direction: str,
) -> MacroStateOverlay:
    if redis_client is None or instrument not in _PAIR_CURRENCIES or direction not in {"LONG", "SHORT"}:
        return MacroStateOverlay(regime="UNAVAILABLE")

    try:
        rate_raw = await redis_client.get("fred_rate_diff")
        if rate_raw:
            rate_data = json.loads(rate_raw).get(instrument, {}) or {}
        else:
            rate_data = {}

        base_ccy, quote_ccy = _PAIR_CURRENCIES[instrument]
        base_surprise_raw = await redis_client.get(f"econ_surprise:{base_ccy}")
        quote_surprise_raw = await redis_client.get(f"econ_surprise:{quote_ccy}")
        base_surprise = float(json.loads(base_surprise_raw)) if base_surprise_raw else 0.0
        quote_surprise = float(json.loads(quote_surprise_raw)) if quote_surprise_raw else 0.0

        cross_raw = await redis_client.get("cross_asset_risk")
        cross_payload = json.loads(cross_raw) if cross_raw else {}
        cross_result = cross_payload.get("result") if isinstance(cross_payload, dict) else None
        cross_sentiment = float(cross_result.get("risk_sentiment", 0.0)) if cross_result else 0.0
        cross_regime = cross_result.get("regime") if cross_result else None

        rate_diff = float(rate_data.get("rate_diff", 0.0) or 0.0)
        velocity_diff = float(rate_data.get("velocity_diff", 0.0) or 0.0)
        rate_score = max(-1.0, min(1.0, rate_diff / 6.0))
        velocity_score = max(-1.0, min(1.0, velocity_diff / 2.0))
        surprise_score = max(-1.0, min(1.0, (base_surprise - quote_surprise) / 2.0))
        cross_score = max(-1.0, min(1.0, cross_sentiment * float(_RISK_SENSITIVITY.get(instrument, 0.0))))

        composite = (
            0.45 * rate_score
            + 0.20 * velocity_score
            + 0.20 * surprise_score
            + 0.15 * cross_score
        )
        bias = _bias_from_score(composite)
        aligned = (
            bias == "NEUTRAL"
            or (bias == "LONG" and direction == "LONG")
            or (bias == "SHORT" and direction == "SHORT")
        )

        if bias == "NEUTRAL":
            multiplier = 1.0
            veto = False
            regime = "NEUTRAL"
        elif aligned and abs(composite) >= 0.45:
            multiplier = 1.0
            veto = False
            regime = "STRONG_ALIGNED"
        elif aligned:
            multiplier = 0.85
            veto = False
            regime = "WEAK_ALIGNED"
        elif abs(composite) >= 0.55:
            multiplier = 0.5
            veto = True
            regime = "STRONG_CONFLICT"
        else:
            multiplier = 0.65
            veto = False
            regime = "CONFLICT"

        return MacroStateOverlay(
            multiplier=multiplier,
            veto=veto,
            regime=regime,
            directional_bias=bias,
            score=composite,
            detail={
                "rate_diff": round(rate_diff, 4),
                "velocity_diff": round(velocity_diff, 4),
                "base_surprise": round(base_surprise, 4),
                "quote_surprise": round(quote_surprise, 4),
                "cross_asset_sentiment": round(cross_sentiment, 4),
                "cross_asset_regime": cross_regime,
                "rate_score": round(rate_score, 4),
                "velocity_score": round(velocity_score, 4),
                "surprise_score": round(surprise_score, 4),
                "cross_asset_score": round(cross_score, 4),
            },
        )
    except Exception as exc:
        logger.warning("macro_state_overlay_failed", instrument=instrument, error=str(exc))
        return MacroStateOverlay(regime="ERROR")
