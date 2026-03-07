from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from anchor.database.engine import get_db
from anchor.database.models import Trade, EquityCurvePoint
from anchor.analytics.performance import compute_performance
from anchor.analytics.monte_carlo import run_monte_carlo

router = APIRouter()


@router.get("/performance/summary")
async def get_performance_summary(session: AsyncSession = Depends(get_db)):
    q = select(Trade).order_by(Trade.closed_at)
    result = await session.execute(q)
    trades = result.scalars().all()
    summary = compute_performance(trades)
    return summary.__dict__


@router.get("/performance/equity-curve")
async def get_equity_curve(
    limit: int = 500,
    session: AsyncSession = Depends(get_db),
):
    q = select(EquityCurvePoint).order_by(desc(EquityCurvePoint.time)).limit(limit)
    result = await session.execute(q)
    points = list(reversed(result.scalars().all()))
    return [
        {
            "time":                p.time.isoformat(),
            "account_balance":     float(p.account_balance),
            "account_equity":      float(p.account_equity),
            "unrealized_pl":       float(p.unrealized_pl),
            "drawdown_pct":        float(p.drawdown_pct) if p.drawdown_pct else None,
            "open_position_count": p.open_position_count,
        }
        for p in points
    ]


@router.get("/performance/monte-carlo")
async def get_monte_carlo(
    n_simulations: int = 10000,
    session: AsyncSession = Depends(get_db),
):
    q = select(Trade.net_pl).order_by(Trade.closed_at)
    result = await session.execute(q)
    pnl_values = [float(row[0]) for row in result.all()]

    if not pnl_values:
        return {"error": "No trades available for Monte Carlo simulation"}

    mc = run_monte_carlo(pnl_values, n_simulations=n_simulations)
    return mc.__dict__


@router.get("/performance/trade-journal")
async def get_trade_journal(
    limit:      int = 200,
    instrument: str | None = None,
    session:    AsyncSession = Depends(get_db),
):
    q = select(Trade).order_by(desc(Trade.closed_at)).limit(limit)
    if instrument:
        q = q.where(Trade.instrument == instrument)
    result = await session.execute(q)
    trades = result.scalars().all()
    return [
        {
            "id":            str(t.id),
            "instrument":    t.instrument,
            "direction":     t.direction,
            "units":         float(t.units),
            "entry_price":   float(t.entry_price),
            "exit_price":    float(t.exit_price),
            "opened_at":     t.opened_at.isoformat(),
            "closed_at":     t.closed_at.isoformat(),
            "net_pl":        float(t.net_pl),
            "gross_pl":      float(t.gross_pl),
            "commission":    float(t.commission),
            "close_reason":  t.close_reason,
            "regime":        t.regime_at_entry,
            "session":       t.session_at_entry,
        }
        for t in trades
    ]
