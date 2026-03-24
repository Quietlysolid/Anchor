"""Paper-only NFP continuation signal engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.config import get_settings
from anchor.database.models import EconomicEvent
from anchor.signals.economic_surprise import _PAIR_CURRENCIES, _parse_value, _surprise_sign
from anchor.utils.time_utils import utcnow

logger = structlog.get_logger(__name__)
settings = get_settings()

NFP_SESSION = "NFP_DRIFT"
NFP_EVENT = "Non-Farm Employment Change"
UNEMPLOYMENT_EVENT = "Unemployment Rate"
EARNINGS_EVENT = "Average Hourly Earnings m/m"
NFP_BIG_SURPRISE_THRESHOLD = 0.20
NFP_HOLD_HOURS = 24


@dataclass
class NfpReleaseBundle:
    event_time: pd.Timestamp
    nfp: EconomicEvent
    unemployment: EconomicEvent | None
    earnings: EconomicEvent | None


@dataclass
class NfpSignalResult:
    instrument: str
    direction: str | None = None
    confluence_score: float = 0.0
    event_time_utc: datetime | None = None
    entry_time_utc: datetime | None = None
    expected_exit_time_utc: datetime | None = None
    agreement: str | None = None
    surprise_bucket: str | None = None
    support_count: int = 0
    conflict_count: int = 0
    surprise_mag: float | None = None
    session: str = NFP_SESSION
    suppressed: bool = True
    suppression_reason: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    metadata: dict = field(default_factory=dict)


def _utc_timestamp(value: datetime | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _relative_surprise(actual: str | None, forecast: str | None) -> float | None:
    actual_val = _parse_value(actual)
    forecast_val = _parse_value(forecast)
    if actual_val is None or forecast_val is None:
        return None
    scale = abs(forecast_val)
    if scale <= 1e-12:
        return None
    return abs(actual_val - forecast_val) / scale


def _agreement_label(
    nfp_sign: float,
    unemployment_sign: float | None,
    earnings_sign: float | None,
) -> tuple[str, int, int]:
    supports = 0
    conflicts = 0
    for companion in (unemployment_sign, earnings_sign):
        if companion is None:
            continue
        if companion == nfp_sign:
            supports += 1
        elif abs(companion + nfp_sign) < 1e-12:
            conflicts += 1
    if supports >= 1 and conflicts == 0:
        return "ALIGNED", supports, conflicts
    if conflicts >= 1:
        return "CONFLICT", supports, conflicts
    return "SOLO", supports, conflicts


def _usd_direction_for_pair(pair: str, usd_sign: float) -> str | None:
    if abs(usd_sign) < 1e-12:
        return None
    base_ccy, quote_ccy = _PAIR_CURRENCIES[pair]
    if base_ccy == "USD":
        return "LONG" if usd_sign > 0 else "SHORT"
    if quote_ccy == "USD":
        return "SHORT" if usd_sign > 0 else "LONG"
    return None


def _surprise_bucket(surprise_mag: float | None) -> str:
    if surprise_mag is None:
        return "UNKNOWN"
    return "BIG" if surprise_mag >= NFP_BIG_SURPRISE_THRESHOLD else "SMALL"


async def fetch_recent_nfp_bundle(session: AsyncSession, now: datetime, lookback_days: int = 10) -> NfpReleaseBundle | None:
    start_ts = now - timedelta(days=lookback_days)
    result = await session.execute(
        select(EconomicEvent)
        .where(
            and_(
                EconomicEvent.currency == "USD",
                EconomicEvent.impact == "HIGH",
                EconomicEvent.event_time >= start_ts,
                EconomicEvent.event_time <= now,
                EconomicEvent.event_name.in_([NFP_EVENT, UNEMPLOYMENT_EVENT, EARNINGS_EVENT]),
                EconomicEvent.actual.isnot(None),
                EconomicEvent.forecast.isnot(None),
            )
        )
        .order_by(EconomicEvent.event_time.desc(), EconomicEvent.event_name)
    )
    rows = list(result.scalars().all())

    grouped: dict[pd.Timestamp, dict[str, EconomicEvent]] = {}
    for event in rows:
        event_ts = _utc_timestamp(event.event_time)
        grouped.setdefault(event_ts, {})[event.event_name] = event

    for event_ts in sorted(grouped.keys(), reverse=True):
        row_map = grouped[event_ts]
        nfp = row_map.get(NFP_EVENT)
        if nfp is None:
            continue
        return NfpReleaseBundle(
            event_time=event_ts,
            nfp=nfp,
            unemployment=row_map.get(UNEMPLOYMENT_EVENT),
            earnings=row_map.get(EARNINGS_EVENT),
        )
    return None


class NfpContinuationEngine:
    def __init__(self, data_cache: dict[str, dict[str, pd.DataFrame]] | None = None) -> None:
        self.data_cache = data_cache or {}

    def update_cache(self, instrument: str, timeframe: str, df: pd.DataFrame) -> None:
        self.data_cache.setdefault(timeframe, {})[instrument] = df

    def _get_data(self, instrument: str, timeframe: str) -> pd.DataFrame | None:
        return self.data_cache.get(timeframe, {}).get(instrument)

    def evaluate(
        self,
        instrument: str,
        bundle: NfpReleaseBundle | None,
        dt: datetime | None = None,
    ) -> NfpSignalResult:
        dt = dt or utcnow()
        result = NfpSignalResult(instrument=instrument, created_at=dt)

        if instrument not in set(settings.nfp_instruments):
            result.suppression_reason = f"NFP_INSTRUMENT_EXCLUDED:{instrument}"
            return result
        if bundle is None:
            result.suppression_reason = "NFP_NO_RECENT_BUNDLE"
            return result

        df = self._get_data(instrument, "H1")
        if df is None or len(df) < 30:
            result.suppression_reason = "NFP_INSUFFICIENT_DATA"
            return result

        nfp_sign = _surprise_sign(bundle.nfp.actual, bundle.nfp.forecast)
        if nfp_sign is None or abs(nfp_sign) < 1e-12:
            result.suppression_reason = "NFP_ZERO_SURPRISE"
            return result

        unemployment_sign = None
        if bundle.unemployment is not None:
            raw_sign = _surprise_sign(bundle.unemployment.actual, bundle.unemployment.forecast)
            unemployment_sign = (-raw_sign) if raw_sign is not None else None

        earnings_sign = None
        if bundle.earnings is not None:
            earnings_sign = _surprise_sign(bundle.earnings.actual, bundle.earnings.forecast)

        agreement, support_count, conflict_count = _agreement_label(
            nfp_sign, unemployment_sign, earnings_sign
        )
        if agreement != "ALIGNED":
            result.suppression_reason = f"NFP_NOT_ALIGNED:{agreement}"
            return result

        surprise_mag = _relative_surprise(bundle.nfp.actual, bundle.nfp.forecast)
        surprise_bucket = _surprise_bucket(surprise_mag)
        if surprise_bucket != "BIG":
            result.suppression_reason = f"NFP_NOT_BIG:{surprise_bucket}"
            return result

        direction = _usd_direction_for_pair(instrument, nfp_sign)
        if direction is None:
            result.suppression_reason = "NFP_DIRECTION_UNAVAILABLE"
            return result

        entry_idx = df.index.searchsorted(bundle.event_time, side="right")
        if entry_idx >= len(df.index):
            result.suppression_reason = "NFP_ENTRY_BAR_MISSING"
            return result
        entry_time = pd.Timestamp(df.index[entry_idx])
        now_ts = _utc_timestamp(dt)
        if not (entry_time <= now_ts < entry_time + pd.Timedelta(hours=1)):
            result.suppression_reason = f"NFP_OFF_WINDOW:{now_ts.isoformat()}"
            return result

        result.direction = direction
        result.confluence_score = 0.8
        result.event_time_utc = bundle.event_time.to_pydatetime()
        result.entry_time_utc = entry_time.to_pydatetime()
        result.expected_exit_time_utc = (entry_time + pd.Timedelta(hours=NFP_HOLD_HOURS)).to_pydatetime()
        result.agreement = agreement
        result.surprise_bucket = surprise_bucket
        result.support_count = support_count
        result.conflict_count = conflict_count
        result.surprise_mag = round(float(surprise_mag or 0.0), 4)
        result.suppressed = False
        result.metadata = {
            "strategy": "NFP_CONTINUATION",
            "event_time_utc": bundle.event_time.isoformat(),
            "entry_time_utc": entry_time.isoformat(),
            "expected_exit_time_utc": (entry_time + pd.Timedelta(hours=NFP_HOLD_HOURS)).isoformat(),
            "agreement": agreement,
            "surprise_bucket": surprise_bucket,
            "support_count": support_count,
            "conflict_count": conflict_count,
            "surprise_mag": round(float(surprise_mag or 0.0), 4),
            "hold_hours": NFP_HOLD_HOURS,
            "nfp_actual": bundle.nfp.actual,
            "nfp_forecast": bundle.nfp.forecast,
        }
        return result
