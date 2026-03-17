"""Data ingestion tasks: candles, FRED rates, COT, VIX, sentiment, order book, CME, FX options, cross-asset."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async

logger = structlog.get_logger(__name__)


@celery_app.task(name="anchor.scheduler.jobs.import_economic_calendar", bind=True, max_retries=2)
def import_economic_calendar(self):
    """Import ForexFactory calendar for next 7 days."""
    async def _inner():
        from anchor.data.forex_factory import ForexFactoryScraper
        from anchor.database.engine import init_db, get_session
        from anchor.database.repositories import EconomicCalendarRepository
        from datetime import date

        await init_db()

        async with ForexFactoryScraper() as scraper:
            events = await scraper.fetch_week()

        if not events:
            return

        async with get_session() as session:
            repo = EconomicCalendarRepository(session)
            count = await repo.insert_many(events)
            await session.commit()
        logger.info("calendar_imported", events=count)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("calendar_import_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.update_fred_rates", bind=True, max_retries=3)
def update_fred_rates(self):
    """Fetch FRED central bank policy rates and store rate differentials in Redis.

    Key: fred_rate_diff  TTL: 25 hours.
    Runs daily — rates change at most once per central bank meeting (~6 weeks).
    """
    async def _inner():
        from anchor.config import settings
        from anchor.data.fred_rates import fetch_and_store, fetch_and_store_dxy
        import redis.asyncio as aioredis

        if not settings.fred_api_key:
            logger.warning("fred_api_key_not_set")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            result = await fetch_and_store(redis_client, settings.fred_api_key)
            available = [k for k, v in result.items() if v["available"]]
            logger.info("fred_rates_updated", available=available)
            await fetch_and_store_dxy(redis_client, settings.fred_api_key)
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("fred_rates_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=3_600)


@celery_app.task(name="anchor.scheduler.jobs.update_cot_data", bind=True, max_retries=2)
def update_cot_data(self):
    """Download and store latest CFTC COT report in Redis.

    Key: cot_data  TTL: 8 days (COT is weekly; 8-day TTL survives holiday delays).
    """
    async def _inner():
        import json
        from anchor.config import settings
        from anchor.data.cot_parser import CotParser
        import redis.asyncio as aioredis

        async with CotParser() as parser:
            result = await parser.fetch_latest()

        if not result:
            logger.warning("cot_empty_result")
            return

        # Serialise datetime → ISO strings so json.dumps works
        serialisable = {
            currency: {
                "net_noncommercial": data["net_noncommercial"],
                "net_commercial":    data["net_commercial"],
                "report_date":       data["report_date"].isoformat() if data.get("report_date") else None,
            }
            for currency, data in result.items()
        }

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis_client.set("cot_data", json.dumps(serialisable), ex=8 * 86_400)

            # Translate raw COT numbers into plain-English directional bias per currency
            try:
                from anchor.intelligence.trade_intelligence import interpret_cot as _interpret_cot
                interpreted, _tok = await _interpret_cot(serialisable)
                if interpreted:
                    await redis_client.set(
                        "cot_interpretation",
                        json.dumps(interpreted),
                        ex=8 * 86_400,
                    )
                    logger.info("cot_interpretation_stored", currencies=list(interpreted.keys()), tokens=_tok)
            except Exception as _ci_exc:
                logger.warning("cot_interpretation_failed", error=str(_ci_exc))
        finally:
            await redis_client.aclose()

        logger.info("cot_persisted", currencies=list(result.keys()))

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("cot_update_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=600)


@celery_app.task(name="anchor.scheduler.jobs.update_economic_surprise", bind=True, max_retries=2)
def update_economic_surprise(self):
    """Compute per-currency economic surprise scores and cache in Redis.

    Reads recent HIGH-impact releases (actual vs forecast) from the DB,
    computes a rolling surprise score per currency, and writes to Redis.
    Keys: econ_surprise:{CCY}  TTL: 1 hour.
    Runs every 30 minutes — often no new releases, but cost is a single DB read.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.database.engine import init_db, get_session
        from anchor.database.repositories.events import EconomicCalendarRepository
        from anchor.signals.economic_surprise import update_surprise_cache
        import redis.asyncio as aioredis

        await init_db()
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            async with get_session() as session:
                repo = EconomicCalendarRepository(session)
                await update_surprise_cache(redis_client, repo)
            logger.info("economic_surprise_cache_updated")
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("economic_surprise_update_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="anchor.scheduler.jobs.import_candles", bind=True, max_retries=3)
def import_candles(self):
    """Import latest H1, H4, and D candles from OANDA for all instruments.

    Runs every hour to keep the candle cache fresh for signal scanning.
    Fetches only the last ~200 H1 / 100 H4 / 60 D candles (fast incremental).
    """
    async def _inner():
        from anchor.config import settings
        from anchor.database.engine import init_db
        from anchor.database.repositories.market_data import MarketDataRepository
        from anchor.data.oanda_history import OANDAHistoryClient

        await init_db()
        import anchor.database.engine as _db_engine

        client = OANDAHistoryClient()
        end = datetime.now(timezone.utc)

        # How far back to pull per timeframe (enough to keep signal scan fed)
        lookback = {
            "M15": timedelta(days=3),   # ~288 M15 bars (3 days × 96 bars/day)
            "H1":  timedelta(days=9),   # ~200 H1 bars
            "H4":  timedelta(days=17),  # ~100 H4 bars
            "D":   timedelta(days=270), # ~250 D bars (need 200+ for SMA(200) in MTF check)
        }

        for instrument in settings.instruments:
            for tf, delta in lookback.items():
                try:
                    df = await client.fetch_candles(
                        instrument=instrument,
                        granularity=tf,
                        start=end - delta,
                        end=end,
                    )
                    if df.empty:
                        continue

                    async with _db_engine.AsyncSessionFactory() as session:
                        repo = MarketDataRepository(session)
                        await repo.bulk_insert(instrument, tf, df)
                        await session.commit()

                    logger.info(
                        "candles_imported",
                        instrument=instrument,
                        timeframe=tf,
                        rows=len(df),
                    )
                except Exception as exc:
                    logger.error(
                        "candle_import_failed",
                        instrument=instrument,
                        timeframe=tf,
                        error=str(exc),
                    )

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("import_candles_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="anchor.scheduler.jobs.update_vix", bind=True, max_retries=3)
def update_vix(self):
    """Fetch latest VIX from FRED and cache in Redis.

    Key: vix_latest  TTL: 4 hours.
    VIX is daily — fetching every 4 h ensures the morning's value is available
    throughout the London/NY session. Fail-open: returns 1.0 multiplier on miss.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.signals.vix_filter import fetch_and_store
        import redis.asyncio as aioredis

        if not settings.fred_api_key:
            logger.warning("fred_api_key_not_set_for_vix")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            vix = await fetch_and_store(redis_client, settings.fred_api_key)
            logger.info("vix_updated", vix=vix)
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_vix_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=3_600)


@celery_app.task(name="anchor.scheduler.jobs.update_oanda_sentiment", bind=True, max_retries=3)
def update_oanda_sentiment(self):
    """Fetch OANDA positionBook for all instruments and cache in Redis.

    Key: oanda_sentiment:{instrument}  TTL: 5 minutes per instrument.
    Run every 5 minutes during market hours so signal scan always has fresh
    sentiment data. Fail-open: engine returns 0.5 (neutral) on cache miss.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.signals.oanda_sentiment import fetch_and_store
        import redis.asyncio as aioredis

        if not settings.oanda_api_key:
            logger.warning("oanda_api_key_not_set_for_sentiment")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            for instrument in settings.instruments:
                try:
                    long_pct = await fetch_and_store(
                        redis_client,
                        instrument,
                        settings.oanda_api_key,
                        settings.oanda_base_url,
                    )
                    logger.debug("oanda_sentiment_updated", instrument=instrument, long_pct=long_pct)
                except Exception as exc:
                    logger.warning("oanda_sentiment_instrument_failed", instrument=instrument, error=str(exc))
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_oanda_sentiment_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="anchor.scheduler.jobs.update_order_book", bind=True, max_retries=3)
def update_order_book(self):
    """Fetch OANDA orderBook for all instruments and cache in Redis.

    Key: order_book:{instrument}  TTL: 5 minutes per instrument.
    Identifies where pending orders (stops + limits) cluster near current price.
    Runs every 5 minutes, aligned with signal scan. Fail-open: 0.5 on miss.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.signals.order_book_signal import fetch_and_store
        import redis.asyncio as aioredis

        if not settings.oanda_api_key:
            logger.warning("oanda_api_key_not_set_for_order_book")
            return

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            for instrument in settings.instruments:
                try:
                    await fetch_and_store(
                        redis_client,
                        instrument,
                        settings.oanda_api_key,
                        settings.oanda_base_url,
                    )
                except Exception as exc:
                    logger.warning("order_book_instrument_failed", instrument=instrument, error=str(exc))
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_order_book_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="anchor.scheduler.jobs.update_cme_flow", bind=True, max_retries=2)
def update_cme_flow(self):
    """Fetch CME FX futures OI + volume data and cache in Redis.

    Key: cme_flow:{instrument}  TTL: 2 hours per instrument.
    Uses yfinance to pull 6E/6B/6J daily data. Runs every 2 hours.
    Fail-open: 0.5 on miss. Requires yfinance to be installed.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.data.cme_flow import fetch_and_store, _CME_MAP
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            for instrument in settings.instruments:
                if instrument not in _CME_MAP:
                    continue
                try:
                    result = await fetch_and_store(redis_client, instrument)
                    logger.debug("cme_flow_updated", instrument=instrument,
                                 raw_score=result.get("raw_score") if result else None)
                except Exception as exc:
                    logger.warning("cme_flow_instrument_failed", instrument=instrument, error=str(exc))
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_cme_flow_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=1_800)


@celery_app.task(name="anchor.scheduler.jobs.update_fx_options", bind=True, max_retries=2)
def update_fx_options(self):
    """Fetch FX ETF options chain and compute risk-reversal proxy, cache in Redis.

    Key: fx_options_rr:{instrument}  TTL: 4 hours per instrument.
    Uses yfinance options on FXE/FXB/FXY. Runs every 4 hours.
    Fail-open: 0.5 on miss. Requires yfinance to be installed.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.data.fx_options import fetch_and_store, _ETF_MAP
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            for instrument in settings.instruments:
                if instrument not in _ETF_MAP:
                    continue
                try:
                    result = await fetch_and_store(redis_client, instrument)
                    logger.debug("fx_options_rr_updated", instrument=instrument,
                                 raw_score=result.get("raw_score") if result else None)
                except Exception as exc:
                    logger.warning("fx_options_instrument_failed", instrument=instrument, error=str(exc))
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_fx_options_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=3_600)


@celery_app.task(name="anchor.scheduler.jobs.update_cross_asset", bind=True, max_retries=2)
def update_cross_asset(self):
    """Fetch SPY + GLD via yfinance, compute risk-on/off sentiment, cache in Redis.

    Key: cross_asset_risk  TTL: 2 hours.
    Drives the cross_asset confluence component for AUD, NZD, CAD, EUR_JPY.
    """
    async def _inner():
        from anchor.config import settings
        from anchor.data.cross_asset import fetch_and_store
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            result = await fetch_and_store(redis_client)
            logger.info("cross_asset_updated",
                        regime=result.get("regime") if result else None,
                        sentiment=result.get("risk_sentiment") if result else None)
        finally:
            await redis_client.aclose()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("update_cross_asset_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=1_800)
