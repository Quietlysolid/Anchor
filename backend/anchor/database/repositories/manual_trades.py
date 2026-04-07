from __future__ import annotations

from decimal import Decimal
from typing import List

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import ManualTradeJournal
from anchor.utils.time_utils import utcnow


class ManualTradeJournalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_recent(self, limit: int = 250) -> List[ManualTradeJournal]:
        result = await self.session.execute(
            select(ManualTradeJournal)
            .order_by(desc(ManualTradeJournal.updated_at), desc(ManualTradeJournal.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        action_key: str,
        action: str,
        instrument: str,
        market: str | None,
        direction: str | None,
        contracts: int,
        reason: str | None,
        anchor_generated_at,
        anchor_reference_price: float | None,
        anchor_stop_price: float | None,
        anchor_entry_note: str | None,
        anchor_exit_note: str | None,
        taken: bool,
        closed: bool,
        fill_price: float | None,
        stop_price: float | None,
        exit_price: float | None,
        notes: str | None,
    ) -> ManualTradeJournal:
        result = await self.session.execute(
            select(ManualTradeJournal).where(ManualTradeJournal.action_key == action_key).limit(1)
        )
        row = result.scalar_one_or_none()

        if row is None:
            row = ManualTradeJournal(
                action_key=action_key,
                action=action,
                instrument=instrument,
                market=market,
                direction=direction,
                contracts=contracts,
            )
            self.session.add(row)

        row.updated_at = utcnow()
        row.action = action
        row.instrument = instrument
        row.market = market
        row.direction = direction
        row.contracts = contracts
        row.reason = reason
        row.anchor_generated_at = anchor_generated_at
        row.anchor_reference_price = Decimal(str(anchor_reference_price)) if anchor_reference_price is not None else None
        row.anchor_stop_price = Decimal(str(anchor_stop_price)) if anchor_stop_price is not None else None
        row.anchor_entry_note = anchor_entry_note
        row.anchor_exit_note = anchor_exit_note
        row.taken = taken
        row.closed = closed
        row.fill_price = Decimal(str(fill_price)) if fill_price is not None else None
        row.stop_price = Decimal(str(stop_price)) if stop_price is not None else None
        row.exit_price = Decimal(str(exit_price)) if exit_price is not None else None
        row.notes = notes

        await self.session.flush()
        await self.session.refresh(row)
        return row
