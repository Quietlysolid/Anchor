"""Intelligence reports API — pre/post-session briefs and weekly synthesis."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.config import get_settings
from anchor.database.engine import get_db
from anchor.database.models import IntelligenceReport
from anchor.intelligence.session_quality import DEFAULT as _SQ_DEFAULT

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
    except Exception:
        pass
    finally:
        await redis_client.aclose()
    return dict(_SQ_DEFAULT)
