"""Market context endpoints: DXY, ATR profile, correlation matrix."""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.config import get_settings
from anchor.database.engine import get_db
from anchor.database.models import MarketData
from anchor.utils.time_utils import utcnow

router  = APIRouter()
settings = get_settings()

_ATR_PERIOD  = 14
_ATR_H1_DAYS = 60     # how far back to sample for ATR profile
_CORR_DAYS   = 30     # rolling window for correlation


def _compute_atr(df: pd.DataFrame, period: int = _ATR_PERIOD) -> pd.Series:
    """True Range ATR using high/low/close."""
    hi = df["high"]; lo = df["low"]; pc = df["close"].shift(1)
    tr = pd.concat([hi - lo, (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


@router.get("/market/context")
async def get_market_context(session: AsyncSession = Depends(get_db)):
    """Combined DXY + ATR profile + correlation in one call."""
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        dxy_raw = await redis_client.get("dxy_data")
        vix_raw = await redis_client.get("vix_latest")
    finally:
        await redis_client.aclose()

    dxy = json.loads(dxy_raw) if dxy_raw else None
    vix = json.loads(vix_raw) if vix_raw else None

    instruments = settings.instruments
    now         = utcnow()
    since_atr   = now - timedelta(days=_ATR_H1_DAYS)
    since_corr  = now - timedelta(days=_CORR_DAYS + 5)

    # ── Fetch H1 data for ATR profile ─────────────────────────────
    atr_profile: dict = {}
    closes_daily: dict[str, pd.Series] = {}

    for instr in instruments:
        q = (
            select(MarketData)
            .where(and_(
                MarketData.instrument == instr,
                MarketData.timeframe  == "H1",
                MarketData.time       >= since_atr,
            ))
            .order_by(MarketData.time)
        )
        rows = (await session.execute(q)).scalars().all()
        if len(rows) < _ATR_PERIOD + 5:
            continue

        df = pd.DataFrame([{
            "time":  r.time,
            "high":  float(r.high),
            "low":   float(r.low),
            "close": float(r.close),
        } for r in rows])

        atr = _compute_atr(df).dropna()
        if atr.empty:
            continue

        # Pip size for the instrument
        pip = 0.01 if "JPY" in instr else 0.0001
        current_atr_pips = round(float(atr.iloc[-1]) / pip, 1)
        avg_atr_pips     = round(float(atr.mean()) / pip, 1)
        ratio            = round(current_atr_pips / avg_atr_pips, 2) if avg_atr_pips > 0 else 1.0
        status = "VOLATILE" if ratio > 1.4 else ("QUIET" if ratio < 0.65 else "NORMAL")

        atr_profile[instr] = {
            "current_atr_pips": current_atr_pips,
            "avg_atr_pips":     avg_atr_pips,
            "ratio":            ratio,
            "status":           status,
        }

        # Collect daily closes for correlation
        df["date"] = df["time"].dt.date
        closes_daily[instr] = df.groupby("date")["close"].last()

    # ── Correlation matrix ─────────────────────────────────────────
    correlation: dict = {}
    if len(closes_daily) >= 2:
        close_df = pd.DataFrame(closes_daily).dropna()
        if len(close_df) >= 5:
            ret_df  = close_df.pct_change().dropna()
            corr_mx = ret_df.corr().round(2)
            for i in corr_mx.index:
                correlation[i] = {}
                for j in corr_mx.columns:
                    correlation[i][j] = float(corr_mx.loc[i, j])

    return {
        "dxy":         dxy,
        "vix":         vix,
        "atr_profile": atr_profile,
        "correlation": correlation,
    }
