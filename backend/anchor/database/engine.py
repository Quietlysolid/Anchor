from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from anchor.config import get_settings

settings = get_settings()

engine: AsyncEngine | None = None
AsyncSessionFactory: async_sessionmaker | None = None


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    global engine, AsyncSessionFactory
    engine = create_async_engine(
        settings.database_url,
        echo=settings.app_env == "development",
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=False,
        pool_recycle=1800,
    )

    # asyncpg does not reliably send ROLLBACK on connection return via the
    # default pool_reset_on_return mechanism, leaving connections idle in
    # transaction and exhausting the pool. Force an explicit synchronous
    # rollback at the raw DBAPI level whenever a connection is checked back in.
    @event.listens_for(engine.sync_engine, "reset")
    def _reset_on_return(dbapi_conn, connection_record, reset_state):
        dbapi_conn.rollback()

    AsyncSessionFactory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def close_db() -> None:
    global engine
    if engine:
        await engine.dispose()
        engine = None


@asynccontextmanager
async def get_session() -> AsyncSession:
    if AsyncSessionFactory is None:
        raise RuntimeError("Database not initialized")
    session = AsyncSessionFactory()
    try:
        yield session
    finally:
        try:
            await session.rollback()
        except Exception:
            pass
        await session.close()


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an AsyncSession, auto-closes on request end."""
    if AsyncSessionFactory is None:
        raise RuntimeError("Database not initialized")
    session = AsyncSessionFactory()
    try:
        yield session
    finally:
        try:
            await session.rollback()
        except Exception:
            pass
        await session.close()
