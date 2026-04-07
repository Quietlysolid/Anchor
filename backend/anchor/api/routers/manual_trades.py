from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.api.schemas import (
    ManualTradeJournalResponse,
    ManualTradeJournalUpsertRequest,
    ManualTradingProfileResponse,
    ManualTradingProfileUpsertRequest,
)
from anchor.database.engine import get_db
from anchor.database.repositories.manual_trades import ManualTradeJournalRepository, ManualTradingProfileRepository

router = APIRouter()


def _row_payload(row) -> ManualTradeJournalResponse:
    return ManualTradeJournalResponse(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        action_key=row.action_key,
        action=row.action,
        instrument=row.instrument,
        market=row.market,
        direction=row.direction,
        contracts=row.contracts,
        reason=row.reason,
        anchor_generated_at=row.anchor_generated_at,
        anchor_reference_price=float(row.anchor_reference_price) if row.anchor_reference_price is not None else None,
        anchor_stop_price=float(row.anchor_stop_price) if row.anchor_stop_price is not None else None,
        anchor_entry_note=row.anchor_entry_note,
        anchor_exit_note=row.anchor_exit_note,
        taken=bool(row.taken),
        closed=bool(row.closed),
        filled_on=row.filled_on,
        closed_on=row.closed_on,
        fill_price=float(row.fill_price) if row.fill_price is not None else None,
        stop_price=float(row.stop_price) if row.stop_price is not None else None,
        exit_price=float(row.exit_price) if row.exit_price is not None else None,
        notes=row.notes,
    )


def _profile_payload(row) -> ManualTradingProfileResponse:
    return ManualTradingProfileResponse(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        starting_balance=float(row.starting_balance) if row.starting_balance is not None else None,
    )


@router.get("/manual-trades", response_model=list[ManualTradeJournalResponse])
async def list_manual_trades(session: AsyncSession = Depends(get_db)):
    repo = ManualTradeJournalRepository(session)
    rows = await repo.get_recent()
    return [_row_payload(row) for row in rows]


@router.post("/manual-trades", response_model=ManualTradeJournalResponse)
async def upsert_manual_trade(
    payload: ManualTradeJournalUpsertRequest,
    session: AsyncSession = Depends(get_db),
):
    repo = ManualTradeJournalRepository(session)
    row = await repo.upsert(
        action_key=payload.action_key,
        action=payload.action,
        instrument=payload.instrument,
        market=payload.market,
        direction=payload.direction,
        contracts=payload.contracts,
        reason=payload.reason,
        anchor_generated_at=payload.anchor_generated_at,
        anchor_reference_price=payload.anchor_reference_price,
        anchor_stop_price=payload.anchor_stop_price,
        anchor_entry_note=payload.anchor_entry_note,
        anchor_exit_note=payload.anchor_exit_note,
        taken=payload.taken,
        closed=payload.closed,
        filled_on=payload.filled_on,
        closed_on=payload.closed_on,
        fill_price=payload.fill_price,
        stop_price=payload.stop_price,
        exit_price=payload.exit_price,
        notes=payload.notes,
    )
    await session.commit()
    return _row_payload(row)


@router.delete("/manual-trades/{action_key}")
async def delete_manual_trade(
    action_key: str,
    session: AsyncSession = Depends(get_db),
):
    repo = ManualTradeJournalRepository(session)
    deleted = await repo.delete_by_action_key(action_key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Manual trade journal entry not found.")
    await session.commit()
    return {"ok": True}


@router.get("/manual-trades/profile", response_model=ManualTradingProfileResponse | None)
async def get_manual_trading_profile(session: AsyncSession = Depends(get_db)):
    repo = ManualTradingProfileRepository(session)
    row = await repo.get()
    return _profile_payload(row) if row is not None else None


@router.post("/manual-trades/profile", response_model=ManualTradingProfileResponse)
async def upsert_manual_trading_profile(
    payload: ManualTradingProfileUpsertRequest,
    session: AsyncSession = Depends(get_db),
):
    repo = ManualTradingProfileRepository(session)
    row = await repo.upsert(starting_balance=payload.starting_balance)
    await session.commit()
    return _profile_payload(row)
