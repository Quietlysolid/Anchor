"""Backtest API endpoint — async job pattern to avoid 504 timeouts.

POST /backtest/submit  → submits a Celery task, returns {job_id} immediately.
GET  /backtest/status/{job_id} → polls the Celery result backend for status + results.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    instrument: str = "EUR_USD"
    start_date: str = "2020-01-01"
    end_date: Optional[str] = "2024-12-31"
    initial_balance: float = 10_000.0


class SubmitResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    status: str          # "pending" | "running" | "done" | "error"
    result: Optional[Any] = None
    error: Optional[str] = None


@router.post("/submit", response_model=SubmitResponse)
async def submit_backtest(req: BacktestRequest):
    """Submit a backtest job.  Returns a job_id to poll with GET /backtest/status/{job_id}."""
    from anchor.scheduler.jobs import run_backtest

    end = req.end_date or "2099-01-01"
    task = run_backtest.delay(
        req.instrument,
        req.start_date,
        end,
        req.initial_balance,
    )
    return SubmitResponse(job_id=task.id)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def backtest_status(job_id: str):
    """Poll the result of a submitted backtest job."""
    from anchor.scheduler.celery_app import celery_app
    from celery.result import AsyncResult

    result: AsyncResult = celery_app.AsyncResult(job_id)

    if result.state == "PENDING" or result.state == "RECEIVED":
        return StatusResponse(status="pending")
    if result.state == "STARTED" or result.state == "RETRY":
        return StatusResponse(status="running")
    if result.state == "SUCCESS":
        return StatusResponse(status="done", result=result.result)
    if result.state == "FAILURE":
        return StatusResponse(status="error", error=str(result.result))

    return StatusResponse(status=result.state.lower())
