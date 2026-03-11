from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from anchor.database.engine import get_db
from anchor.database.models import Signal

router = APIRouter()


@router.get("/signals/latest")
async def get_latest_signals(
    instrument: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_db),
):
    q = select(Signal).order_by(desc(Signal.created_at)).limit(limit)
    if instrument:
        q = q.where(Signal.instrument == instrument)
    result = await session.execute(q)
    signals = result.scalars().all()
    return [_signal_to_dict(s) for s in signals]


@router.get("/signals/active")
async def get_active_signals(session: AsyncSession = Depends(get_db)):
    from datetime import timedelta
    from anchor.utils.time_utils import utcnow
    cutoff = utcnow() - timedelta(hours=4)
    q = (
        select(Signal)
        .where(Signal.suppressed == False, Signal.created_at >= cutoff)
        .order_by(desc(Signal.created_at))
    )
    result = await session.execute(q)
    signals = result.scalars().all()
    return [_signal_to_dict(s) for s in signals]


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str, session: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException
    q = select(Signal).where(Signal.id == signal_id)
    result = await session.execute(q)
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return _signal_to_dict(signal)


def _signal_to_dict(s: Signal) -> dict:
    meta = s.signal_metadata or {}
    return {
        "id":                    str(s.id),
        "created_at":            s.created_at.isoformat(),
        "instrument":            s.instrument,
        "timeframe":             s.timeframe,
        "direction":             s.direction,
        "confluence_score":      float(s.confluence_score),
        "rsi_score":             float(s.rsi_score)     if s.rsi_score     else None,
        "bb_kc_score":           float(s.bb_kc_score)   if s.bb_kc_score   else None,
        "adx_score":             float(s.adx_score)     if s.adx_score     else None,
        "sr_score":              float(s.sr_score)       if s.sr_score      else None,
        "mtf_score":             float(s.mtf_score)     if s.mtf_score     else None,
        "csi_score":             float(s.csi_score)     if s.csi_score     else None,
        "cot_score":             float(meta["cot_score"])             if meta.get("cot_score")             is not None else None,
        "rate_divergence_score": float(meta["rate_divergence_score"]) if meta.get("rate_divergence_score") is not None else None,
        "ml_confidence":         float(s.ml_confidence) if s.ml_confidence else None,
        "regime_state":          s.regime_state,
        "session":               s.session,
        "suppressed":            s.suppressed,
        "suppression_reason":    s.suppression_reason,
    }
