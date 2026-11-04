"""Shared transaction handling of the application-database repositories."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class KnowledgeStorageUnavailable(RuntimeError):
    """The application database could not be reached or did not answer in time."""


class Repository:
    """Base of repositories that own short-lived transactions on one session factory."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def _transaction(self, session: AsyncSession | None = None) -> AsyncIterator[AsyncSession]:
        """Borrow the caller’s session or own a transaction that commits on success.

        Raises:
            KnowledgeStorageUnavailable: The connection failed, the pool had no
                free connection, or a lock or statement timeout expired.
        """

        if session is not None:
            yield session
            return
        try:
            async with self._sessions() as owned, owned.begin():
                yield owned
        except (OperationalError, InterfaceError, PoolTimeoutError, OSError) as exc:
            raise KnowledgeStorageUnavailable("The application database is unavailable") from exc


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint or unique index an integrity error reports, if any."""

    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def json_text(value: Any) -> str:
    """Encode a value for a `CAST(... AS jsonb)` parameter, keeping non-ASCII text."""

    return json.dumps(value, ensure_ascii=False, default=str)
