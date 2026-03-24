"""Positioning and crowding overlays.

Current implementation uses weekly CFTC COT data as a sizing/veto overlay,
not as a raw entry signal. This matches the research conclusion: crowded
speculative positioning is useful for reducing left-tail risk, but not as a
standalone directional trigger.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

from anchor.signals.cot_signal import INSTRUMENT_CCY_MAP, NET_POSITION_CAP

logger = structlog.get_logger(__name__)

_REDUCE_THRESH = 0.60
_EXTREME_THRESH = 0.85
_STALE_DAYS = 10


@dataclass
class PositioningOverlay:
    source: str = "COT"
    multiplier: float = 1.0
    veto: bool = False
    regime: str = "NEUTRAL"
    detail: dict | None = None

    def to_metadata(self) -> dict:
        return {
            "source": self.source,
            "multiplier": round(self.multiplier, 4),
            "veto": self.veto,
            "regime": self.regime,
            **(self.detail or {}),
        }


async def get_cot_positioning_overlay(
    redis_client,
    instrument: str,
    direction: str,
) -> PositioningOverlay:
    """Return a COT crowding overlay for the given pair/direction."""
    if redis_client is None:
        return PositioningOverlay(regime="UNAVAILABLE")

    mapping = INSTRUMENT_CCY_MAP.get(instrument)
    if mapping is None or direction not in {"LONG", "SHORT"}:
        return PositioningOverlay(regime="UNSUPPORTED")

    base_ccy, quote_ccy, inverted = mapping

    try:
        raw = await redis_client.get("cot_data")
        if not raw:
            return PositioningOverlay(regime="UNAVAILABLE")

        cot = json.loads(raw)
        base_data = cot.get(base_ccy, {}) or {}
        quote_data = cot.get(quote_ccy, {}) or {}

        base_net = float(base_data.get("net_noncommercial", 0) or 0)
        quote_net = float(quote_data.get("net_noncommercial", 0) or 0)
        net = base_net - quote_net
        if inverted:
            net = -net

        net_norm = max(-1.0, min(1.0, net / NET_POSITION_CAP))
        alignment = net_norm if direction == "LONG" else -net_norm

        report_dates = []
        for data in (base_data, quote_data):
            report_date = data.get("report_date")
            if not report_date:
                continue
            try:
                ts = datetime.fromisoformat(str(report_date).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                report_dates.append(ts.astimezone(timezone.utc))
            except ValueError:
                continue
        stale_days = None
        if report_dates:
            latest = max(report_dates)
            stale_days = (datetime.now(timezone.utc) - latest).days

        detail = {
            "base_currency": base_ccy,
            "quote_currency": quote_ccy,
            "net_noncommercial_base": int(base_net),
            "net_noncommercial_quote": int(quote_net),
            "pair_net_norm": round(net_norm, 4),
            "directional_alignment": round(alignment, 4),
            "stale_days": stale_days,
        }

        if stale_days is not None and stale_days > _STALE_DAYS:
            return PositioningOverlay(regime="STALE", detail=detail)

        if alignment <= -_EXTREME_THRESH:
            return PositioningOverlay(multiplier=0.5, veto=True, regime="EXTREME_CONFLICT", detail=detail)
        if alignment <= -_REDUCE_THRESH:
            return PositioningOverlay(multiplier=0.5, veto=False, regime="CONFLICT", detail=detail)
        if alignment >= _EXTREME_THRESH:
            return PositioningOverlay(multiplier=0.5, veto=False, regime="EXTREME_ALIGNED", detail=detail)
        if alignment >= _REDUCE_THRESH:
            return PositioningOverlay(multiplier=0.75, veto=False, regime="CROWDED_ALIGNED", detail=detail)
        return PositioningOverlay(multiplier=1.0, veto=False, regime="NEUTRAL", detail=detail)

    except Exception as exc:
        logger.warning("cot_positioning_overlay_failed", instrument=instrument, error=str(exc))
        return PositioningOverlay(regime="ERROR")
