"""Async SQLAlchemy engine and session lifecycle."""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from researchflow.core.config import Settings


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    database: str
    pgvector_version: str | None = None


class Database:
    """Own the process-wide engine while yielding one session per operation."""

    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url(),
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        return self.session_factory()

    async def readiness(self) -> ReadinessResult:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                version = await connection.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
        except SQLAlchemyError:
            return ReadinessResult(ready=False, database="unavailable")

        if version is None:
            return ReadinessResult(ready=False, database="available_pgvector_missing")
        return ReadinessResult(
            ready=True,
            database="available",
            pgvector_version=str(version),
        )

    async def dispose(self) -> None:
        await self.engine.dispose()
