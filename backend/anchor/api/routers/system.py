
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.config import get_settings
from anchor.database.engine import get_db
from anchor.api.schemas import RolloutConfigResponse
from anchor.utils.time_utils import utcnow

router = APIRouter()
settings = get_settings()

# Set by main.py lifespan after stream starts
_stream_connected: bool = False
_account_balance: float = 0.0
_account_equity: float = 0.0
_last_reconciliation: str | None = None


def set_stream_status(connected: bool) -> None:
    global _stream_connected
    _stream_connected = connected


def set_account_info(balance: float, equity: float, reconciled_at: str | None = None) -> None:
    global _account_balance, _account_equity, _last_reconciliation
    _account_balance = balance
    _account_equity = equity
    if reconciled_at:
        _last_reconciliation = reconciled_at


@router.get("/system/health")
async def health_check(session: AsyncSession = Depends(get_db)):
    """System health endpoint. Used by Docker healthcheck and monitoring."""
    from sqlalchemy import text, select, func
    from anchor.database.models import Position

    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        result = await session.execute(
            select(func.count()).select_from(Position).where(Position.status == "OPEN")
        )
        open_positions = result.scalar() or 0
    except Exception:
        open_positions = 0

    return {
        "status":              "ok" if db_ok else "degraded",
        "db":                  "ok" if db_ok else "error",
        "timestamp":           utcnow().isoformat(),
        "stream_connected":    _stream_connected,
        "account_balance":     _account_balance,
        "account_equity":      _account_equity,
        "open_positions":      open_positions,
        "last_reconciliation": _last_reconciliation,
    }


@router.get("/system/config", response_model=RolloutConfigResponse)
async def get_system_config():
    """Return the active trading rollout configuration."""
    return {
        "instruments": settings.instruments,
        "trend": {
            "enabled": settings.enable_trend_engine,
            "paper_only": settings.trend_paper_only,
            "risk_pct": settings.trend_risk_pct,
        },
        "mean_reversion": {
            "enabled": settings.enable_mr_engine,
            "paper_only": settings.mr_paper_only,
            "risk_pct": settings.mr_risk_pct,
        },
        "lcr": {
            "enabled": settings.enable_lcr_engine,
            "paper_only": settings.lcr_paper_only,
            "risk_pct": settings.lcr_risk_pct,
        },
        "m15": {
            "enabled": settings.enable_m15_engine,
            "paper_only": settings.m15_paper_only,
            "risk_pct": settings.m15_risk_pct,
        },
        "expected_pilot": {
            "instruments": settings.expected_pilot_instruments,
            "trend": {
                "enabled": settings.expected_pilot_trend_enabled,
                "paper_only": settings.expected_pilot_trend_paper_only,
                "risk_pct": settings.expected_pilot_trend_risk_pct,
            },
            "mean_reversion": {
                "enabled": settings.expected_pilot_mr_enabled,
                "paper_only": settings.expected_pilot_mr_paper_only,
                "risk_pct": settings.expected_pilot_mr_risk_pct,
            },
            "lcr": {
                "enabled": settings.expected_pilot_lcr_enabled,
                "paper_only": settings.expected_pilot_lcr_paper_only,
                "risk_pct": settings.expected_pilot_lcr_risk_pct,
            },
            "m15": {
                "enabled": settings.expected_pilot_m15_enabled,
                "paper_only": settings.expected_pilot_m15_paper_only,
                "risk_pct": settings.expected_pilot_m15_risk_pct,
            },
        },
        "max_risk_per_trade_fallback": settings.max_risk_per_trade,
        "min_confluence_score": settings.min_confluence_score,
        "min_ml_confidence": settings.min_ml_confidence,
    }


@router.get("/system/edge-confidence")
async def get_edge_confidence(session: AsyncSession = Depends(get_db)):
    """Latest edge confidence assessment."""
    from sqlalchemy import select, desc
    from anchor.database.models import SystemEvent

    result = await session.execute(
        select(SystemEvent)
        .where(SystemEvent.event_type == "EDGE_CONFIDENCE_CHECK")
        .order_by(desc(SystemEvent.event_at))
        .limit(1)
    )
    event = result.scalars().first()
    if not event:
        return {"confidence": None, "assessed_at": None, "signals": None, "flag_count": 0, "message": None}

    meta = dict(event.metadata_ or {})
    return {
        "confidence":  meta.get("confidence"),
        "assessed_at": event.event_at.isoformat(),
        "flag_count":  meta.get("flag_count", 0),
        "message":     event.message,
        "signals":     meta.get("signals"),
        "previous_confidence": meta.get("previous_confidence"),
    }


@router.get("/system/events")
async def get_system_events(
    severity: str | None = None,
    limit:    int = 100,
    session:  AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, desc
    from anchor.database.models import SystemEvent

    q = select(SystemEvent).order_by(desc(SystemEvent.event_at)).limit(limit)
    if severity:
        severities = [s.strip().upper() for s in severity.split(",")]
        q = q.where(SystemEvent.severity.in_(severities))

    result = await session.execute(q)
    events = result.scalars().all()
    return [
        {
            "id":         e.id,
            "event_at":   e.event_at.isoformat(),
            "event_type": e.event_type,
            "severity":   e.severity,
            "component":  e.component,
            "message":    e.message,
            "metadata":   dict(e.metadata_ or {}),
        }
        for e in events
    ]
