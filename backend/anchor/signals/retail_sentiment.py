"""
Retail sentiment signal placeholder.

Retail-book data is not available from the current IBKR futures stack, so this
module fail-opens with neutral scores.
"""
from __future__ import annotations


def sentiment_signal_score(raw_score: float | None, direction: str) -> float:
    return 0.5 if raw_score is None else max(0.0, min(1.0, 0.5 + raw_score * 0.5))


async def fetch_and_store(*args, **kwargs):
    return None


async def get_cached_score(*args, **kwargs):
    return None
