from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from anchor.database.engine import get_db
from anchor.database.models import Signal, Trade

router = APIRouter()


@router.get("/signals/today")
async def get_today_activity(session: AsyncSession = Depends(get_db)):
    """Return signals evaluated and trades placed today (ET midnight to now)."""
    # ET midnight in UTC
    et_offset = timedelta(hours=-4)  # EDT; EST is -5 but close enough and avoids pytz dep
    now_et = datetime.now(timezone.utc).astimezone(timezone(et_offset))
    et_midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_midnight = et_midnight.astimezone(timezone.utc)

    signals_count = (await session.execute(
        select(func.count()).select_from(Signal).where(
            Signal.created_at >= utc_midnight,
            Signal.session.in_(["LONDON", "NY_LCR", "LDN_FIX", "NFP_DRIFT"]),
        )
    )).scalar_one()

    trades_count = (await session.execute(
        select(func.count()).select_from(Trade).where(Trade.closed_at >= utc_midnight)
    )).scalar_one()

    return {"signals_today": signals_count, "trades_today": trades_count}


@router.get("/signals/weights")
async def get_signal_weights():
    from anchor.signals.engine import WEIGHTS
    from anchor.signals.fix_continuation import FIX_CONFLUENCE_THRESHOLD
    from anchor.signals.london_close_reversion import LCR_CONFLUENCE_THRESHOLD, LCR_WEIGHTS
    from anchor.config import get_settings
    cfg = get_settings()
    return {
        "london": WEIGHTS,
        "lcr":    LCR_WEIGHTS,
        "thresholds": {
            "london": cfg.min_confluence_score,
            "lcr":    LCR_CONFLUENCE_THRESHOLD,
            "fix":    FIX_CONFLUENCE_THRESHOLD,
            "nfp":    0.80,
        },
        "instruments": cfg.instruments,
        "trend_instruments": cfg.trend_instruments,
        "fix_instruments": cfg.fix_instruments,
        "nfp_instruments": cfg.nfp_instruments,
        "risk": {
            "max_risk_per_trade":    cfg.max_risk_per_trade,
            "drawdown_reduce_pct":   cfg.drawdown_reduce_pct,
            "drawdown_halt_pct":     cfg.drawdown_halt_pct,
            "monthly_halt_pct":      cfg.monthly_halt_pct,
            "daily_loss_limit_pct":  cfg.daily_loss_limit_pct,
            "spread_spike_multiplier": cfg.spread_spike_multiplier,
        },
        "targets": {
            "win_rate_good":       0.47,
            "win_rate_warn":       0.40,
            "profit_factor_good":  1.30,
            "profit_factor_warn":  1.00,
            "drawdown_good":       cfg.drawdown_reduce_pct,
            "drawdown_halt":       cfg.drawdown_halt_pct,
            "sharpe_good":         1.0,
            "sharpe_warn":         0.5,
        },
    }


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
        .where(~Signal.suppressed, Signal.created_at >= cutoff)
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
        "order_book_score":      float(meta["order_book_score"])      if meta.get("order_book_score")      is not None else None,
        "cme_flow_score":        float(meta["cme_flow_score"])        if meta.get("cme_flow_score")        is not None else None,
        "fx_options_score":      float(meta["fx_options_score"])      if meta.get("fx_options_score")      is not None else None,
        "econ_surprise_score":   float(meta["econ_surprise_score"])   if meta.get("econ_surprise_score")   is not None else None,
        "cross_asset_score":     float(meta["cross_asset_score"])     if meta.get("cross_asset_score")     is not None else None,
        "news_multiplier":       float(meta["news_multiplier"])       if meta.get("news_multiplier")       is not None else None,
        "london_high":           float(meta["london_high"])           if meta.get("london_high")           is not None else None,
        "london_low":            float(meta["london_low"])            if meta.get("london_low")            is not None else None,
        "london_mid":            float(meta["london_mid"])            if meta.get("london_mid")            is not None else None,
        "position_in_range":     float(meta["position_in_range"])     if meta.get("position_in_range")     is not None else None,
        "fix_time_utc":          meta.get("fix_time_utc"),
        "expected_exit_time_utc": meta.get("expected_exit_time_utc"),
        "month_end_tag":         meta.get("month_end_tag"),
        "pre_move_pips":         float(meta["pre_move_pips"])         if meta.get("pre_move_pips")         is not None else None,
        "atr_pips":              float(meta["atr_pips"])              if meta.get("atr_pips")              is not None else None,
        "actual_entry_time_utc": meta.get("actual_entry_time_utc"),
        "actual_exit_time_utc":  meta.get("actual_exit_time_utc"),
        "actual_entry_price":    float(meta["actual_entry_price"])    if meta.get("actual_entry_price")    is not None else None,
        "actual_exit_price":     float(meta["actual_exit_price"])     if meta.get("actual_exit_price")     is not None else None,
        "entry_slippage_pips":   float(meta["entry_slippage_pips"])   if meta.get("entry_slippage_pips")   is not None else None,
        "realized_ret_pips":     float(meta["realized_ret_pips"])     if meta.get("realized_ret_pips")     is not None else None,
        "max_favorable_pips":    float(meta["max_favorable_pips"])    if meta.get("max_favorable_pips")    is not None else None,
        "max_adverse_pips":      float(meta["max_adverse_pips"])      if meta.get("max_adverse_pips")      is not None else None,
        "continuation_success":  meta.get("continuation_success"),
        "macro_state_overlay":   meta.get("macro_state_overlay"),
        "positioning_overlay":   meta.get("positioning_overlay"),
        "ml_confidence":         float(s.ml_confidence) if s.ml_confidence else None,
        "regime_state":          s.regime_state,
        "session":               s.session,
        "suppressed":            s.suppressed,
        "suppression_reason":    s.suppression_reason,
    }
