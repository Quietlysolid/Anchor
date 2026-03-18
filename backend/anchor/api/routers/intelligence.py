"""Intelligence reports API — pre/post-session briefs and weekly synthesis."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.config import get_settings
from anchor.database.engine import get_db
from anchor.database.models import IntelligenceReport
from anchor.intelligence.session_quality import DEFAULT as _SQ_DEFAULT

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])
settings = get_settings()


@router.get("/latest")
async def get_latest_brief(
    report_type: Optional[str] = Query(None, description="PRESESSION | POSTSESSION | WEEKLY"),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent intelligence report, optionally filtered by type."""
    q = select(IntelligenceReport).order_by(desc(IntelligenceReport.created_at))
    if report_type:
        q = q.where(IntelligenceReport.report_type == report_type.upper())
    result = await db.execute(q.limit(1))
    report = result.scalar_one_or_none()
    if not report:
        return {"report": None}
    return {
        "report": {
            "id":           str(report.id),
            "type":         report.report_type,
            "created_at":   report.created_at.isoformat(),
            "content":      report.content,
            "tokens_used":  report.tokens_used,
            "delivered":    report.delivered_telegram,
        }
    }


@router.get("/history")
async def get_brief_history(
    report_type: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Return recent intelligence reports."""
    q = select(IntelligenceReport).order_by(desc(IntelligenceReport.created_at))
    if report_type:
        q = q.where(IntelligenceReport.report_type == report_type.upper())
    result = await db.execute(q.limit(limit))
    reports = result.scalars().all()
    return {
        "reports": [
            {
                "id":          str(r.id),
                "type":        r.report_type,
                "created_at":  r.created_at.isoformat(),
                "content":     r.content,
                "tokens_used": r.tokens_used,
                "delivered":   r.delivered_telegram,
            }
            for r in reports
        ]
    }


@router.get("/trade-explanations")
async def get_trade_explanations(
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Return recent trade-level AI explanations (latest first)."""
    result = await db.execute(
        select(IntelligenceReport)
        .where(IntelligenceReport.report_type == "TRADE_EXPLANATION")
        .order_by(desc(IntelligenceReport.created_at))
        .limit(limit)
    )
    reports = result.scalars().all()
    return {
        "explanations": [
            {
                "id":          str(r.id),
                "created_at":  r.created_at.isoformat(),
                "content":     r.content,
                "trade_id":    (r.context_snapshot or {}).get("trade_id"),
                "instrument":  (r.context_snapshot or {}).get("instrument"),
                "outcome":     (r.context_snapshot or {}).get("outcome"),
                "net_pl":      (r.context_snapshot or {}).get("net_pl"),
                "session":     (r.context_snapshot or {}).get("session"),
            }
            for r in reports
        ]
    }


@router.get("/journal-analysis")
async def get_journal_analysis(db: AsyncSession = Depends(get_db)):
    """Return the latest weekly journal pattern analysis."""
    result = await db.execute(
        select(IntelligenceReport)
        .where(IntelligenceReport.report_type == "JOURNAL_ANALYSIS")
        .order_by(desc(IntelligenceReport.created_at))
        .limit(1)
    )
    report = result.scalar_one_or_none()
    if not report:
        return {"analysis": None}
    return {
        "analysis": {
            "id":          str(report.id),
            "created_at":  report.created_at.isoformat(),
            "content":     report.content,
            "trade_count": (report.context_snapshot or {}).get("trade_count"),
            "tokens_used": report.tokens_used,
        }
    }


@router.get("/session-quality")
async def get_session_quality():
    """Return the current session quality assessment from Redis (written at 06:30 UTC)."""
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        raw = await redis_client.get("session_quality")
        if raw:
            data = json.loads(raw)
            if "environment" in data and "size_scale" in data:
                return data
    except Exception as exc:
        logger.warning("session_quality_redis_read_failed", error=str(exc))
    finally:
        await redis_client.aclose()
    return dict(_SQ_DEFAULT)
