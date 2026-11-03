"""Runtime connections to the application database (APP_DATABASE_URL).

Serving code uses only APP_DATABASE_URL. MIGRATION_DATABASE_URL is a
setup-owner credential for Alembic and is never read here, and DATABASE_URL
belongs to the knowledge services. Tables are created by migrations, not here.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class AppDatabaseUnavailable(RuntimeError):
    """The application database could not be reached or timed out."""


@asynccontextmanager
async def app_transaction(
    sessions: async_sessionmaker[AsyncSession],
    unavailable: type[AppDatabaseUnavailable] = AppDatabaseUnavailable,
) -> AsyncIterator[AsyncSession]:
    """Open one short transaction that commits on success.

    Args:
        sessions: Session factory of the application database.
        unavailable: Error class raised for connection failures.

    Raises:
        AppDatabaseUnavailable: Connection, lock or statement timeout, or
            pool exhaustion. Other database errors propagate unchanged.
    """

    try:
        async with sessions() as session, session.begin():
            yield session
    except (OperationalError, InterfaceError, PoolTimeoutError, OSError) as exc:
        raise unavailable("The application database is unavailable") from exc


def app_database_url_from_env() -> str | None:
    """Read the runtime application database URL.

    Returns:
        The APP_DATABASE_URL value, or None when it is unset or empty.
    """

    return os.getenv("APP_DATABASE_URL") or None


CONNECT_TIMEOUT_SECONDS = 5
# Bounds on waiting for a free pooled connection, a row lock and one statement.
POOL_TIMEOUT_SECONDS = 10
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "10s"


def create_app_engine(url: str, *, connect_timeout: int = CONNECT_TIMEOUT_SECONDS) -> AsyncEngine:
    """Create a lazily connecting async engine for the application database.

    Args:
        url: SQLAlchemy URL using the psycopg driver.
        connect_timeout: Seconds to wait for a new connection, so requests
            fail promptly while the database is unreachable.

    Returns:
        An engine whose pool checks connections before use, so a restarted
        database does not fail the first request after it returns. Statement
        parameters (such as password hashes) are left out of error messages.
    """

    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        hide_parameters=True,
        connect_args={
            "connect_timeout": connect_timeout,
            "options": f"-c lock_timeout={LOCK_TIMEOUT} -c statement_timeout={STATEMENT_TIMEOUT}",
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create short-lived sessions that keep loaded values after commit."""

    return async_sessionmaker(engine, expire_on_commit=False)
