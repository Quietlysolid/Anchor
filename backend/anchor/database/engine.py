from contextlib import asynccontextmanager

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
        pool_pre_ping=True,
    )
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
    async with AsyncSessionFactory() as session:
        yield session
