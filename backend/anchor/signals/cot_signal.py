"""
COT (Commitment of Traders) signal scorer.

Institutional non-commercial positioning from CFTC weekly report.
Cached in Redis as "cot_data" (JSON, TTL 8 days) by update_cot_data task.

Signal logic (de Prado + Chan — institutional flow as macro trend filter):
  - Non-commercial (speculative) net positioning reveals institutional bias.
  - When specs are heavily net long + price is going LONG → trend confirmed.
  - When specs are heavily net short + price is going SHORT → trend confirmed.
  - Contrarian case (specs at extreme + price going opposite) → penalize.

Score 0.0–1.0:
  1.0 → strong institutional alignment with signal direction
  0.5 → neutral / data unavailable (fail-open, never blocks a signal alone)
  0.0 → institutions positioned opposite to signal direction

Instrument → currency mapping for COT lookup:
  EUR_USD → EUR net vs USD net
  GBP_USD → GBP net vs USD net
  USD_JPY → USD net vs JPY net (inverted — USD_JPY goes UP when JPY is weak)
  AUD_USD → AUD net vs USD net
  USD_CAD → USD net vs CAD net (inverted)
  NZD_USD → NZD net vs USD net
  USD_CHF → USD net vs CHF net (inverted)
  EUR_GBP → EUR net vs GBP net
  GBP_JPY → GBP net vs JPY net
"""
from __future__ import annotations

import json
import structlog

logger = structlog.get_logger(__name__)

# Map instrument → (base_currency, quote_currency, inverted)
# inverted=True means instrument goes UP when quote currency is WEAK
# e.g. USD_JPY goes UP when JPY weakens → use JPY positioning in reverse
INSTRUMENT_CCY_MAP: dict[str, tuple[str, str, bool]] = {
    "EUR_USD": ("EUR", "USD", False),
    "GBP_USD": ("GBP", "USD", False),
    "AUD_USD": ("AUD", "USD", False),
    "NZD_USD": ("NZD", "USD", False),
    "USD_JPY": ("JPY", "USD", True),   # inverted: LONG USD_JPY = SHORT JPY
    "USD_CAD": ("CAD", "USD", True),   # inverted: LONG USD_CAD = SHORT CAD
    "USD_CHF": ("CHF", "USD", True),   # inverted: LONG USD_CHF = SHORT CHF
    "EUR_GBP": ("EUR", "GBP", False),
    "GBP_JPY": ("GBP", "JPY", False),
}

# Extreme positioning threshold (contracts) — above this absolute net value,
# we consider specs to be at an extreme. Typical range: ±50k–200k contracts.
# Using a relative percentile approach: score scales with |net| up to a cap.
# Cap at 150k contracts (approximately 95th percentile historically).
NET_POSITION_CAP = 150_000


async def get_cot_score(
    redis_client,
    instrument: str,
    direction: str,
) -> float:
    """
    Returns a COT alignment score [0.0, 1.0].
    0.5 = neutral / unavailable (fail-open).
    """
    if redis_client is None:
        return 0.5

    mapping = INSTRUMENT_CCY_MAP.get(instrument)
    if mapping is None:
        return 0.5

    base_ccy, quote_ccy, inverted = mapping

    try:
        raw = await redis_client.get("cot_data")
        if not raw:
            return 0.5

        cot = json.loads(raw)

        base_net  = cot.get(base_ccy,  {}).get("net_noncommercial", 0) or 0
        quote_net = cot.get(quote_ccy, {}).get("net_noncommercial", 0) or 0

        # Net positioning of base vs quote currency
        # Positive = speculative community net long the base (bearish quote)
        net = float(base_net) - float(quote_net)

        if inverted:
            # e.g. USD_JPY: LONG = short JPY. COT gives JPY net separately.
            # When specs are net short JPY (negative JPY net), USD_JPY should go UP.
            net = -net

        # Normalize to [-1, 1] range
        net_norm = max(-1.0, min(1.0, net / NET_POSITION_CAP))

        # Convert to directional score
        if direction == "LONG":
            # net_norm > 0 → specs net long base → confirms LONG signal
            score = 0.5 + net_norm * 0.5
        else:
            # net_norm < 0 → specs net short base → confirms SHORT signal
            score = 0.5 - net_norm * 0.5

        return round(max(0.0, min(1.0, score)), 4)

    except Exception as exc:
        logger.warning("cot_score_failed", instrument=instrument, error=str(exc))
        return 0.5
