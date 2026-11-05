"""Judged pairs of knowledge chunks in the application database.

The same rules as `storage/knowledge_conflicts.py`, on the ORM
`knowledge_conflicts` table and scoped to one knowledge base: one row per
ordered pair (`a_id < b_id`) and judge fingerprint. A reviewer's decision
holds for the content it saw; a new judgement replaces it only after either
chunk's content hash changes, and the `review` history is always kept.
Nothing here changes the status of the chunks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.storage.knowledge_conflicts import (
    MACHINE_STATUSES,
    REVIEW_STATUSES,
    STATUSES,
    ConflictRow,
    ordered,
)
from backend.src.storage.orm import KnowledgeConflict, new_id
from backend.src.storage.repositories.base import Repository, json_text
from backend.src.storage.repositories.lifecycle import InactiveKnowledgeBase, lock_kb


__all__ = ["ConflictRepository", "ConflictRow", "ordered"]

_TABLE = KnowledgeConflict.__tablename__
_COLUMNS = ("id", "a_id", "b_id", "a_hash", "b_hash", "judge_fingerprint", "new_id", "relation",
            "aspect", "confidence", "reason", "quotes", "list_verdict", "pair_verdict", "status",
            "linked", "job_id", "review", "created_at", "updated_at")
_JSON_COLUMNS = ("quotes", "list_verdict", "pair_verdict", "review")
# Columns a new judgement replaces; `review` history and creation date stay.
_JUDGED = tuple(
    column for column in _COLUMNS
    if column not in {"id", "a_id", "b_id", "judge_fingerprint", "review", "created_at", "updated_at"}
)
_INSERTED = ("id", "knowledge_base_id", "a_id", "b_id", "judge_fingerprint", *_JUDGED)
_MACHINE_SQL = ", ".join(f"'{status}'" for status in MACHINE_STATUSES)
_REVIEWED_SQL = f"status IN ({', '.join(repr(status) for status in REVIEW_STATUSES)})"
_PAIRS_SQL = "(a_id, b_id) IN (SELECT * FROM unnest(CAST(:a_ids AS uuid[]), CAST(:b_ids AS uuid[])))"
_RECORDS_SQL = "(a_id = ANY(CAST(:ids AS uuid[])) OR b_id = ANY(CAST(:ids AS uuid[])))"


def _row(row: Mapping[str, Any]) -> ConflictRow:
    values = {column: row[column] for column in _COLUMNS}
    values["quotes"] = values["quotes"] or {}
    values["review"] = values["review"] or []
    values["job_id"] = str(values["job_id"]) if values["job_id"] else None
    return ConflictRow(**values)


def _pair_params(pairs: Iterable[tuple[UUID, UUID]]) -> dict[str, list[UUID]]:
    unique = list(dict.fromkeys(pairs))
    return {"a_ids": [a for a, _ in unique], "b_ids": [b for _, b in unique]}


class ConflictRepository(Repository):
    """Conflict rows of the chunks of one knowledge base."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], knowledge_base_id: UUID) -> None:
        super().__init__(sessions)
        self.knowledge_base_id = knowledge_base_id

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[AsyncSession]:
        """Protect the active KB while the caller locks and writes both sides of a pair."""

        async with self._transaction() as session:
            if not await lock_kb(session, self.knowledge_base_id):
                raise InactiveKnowledgeBase()
            yield session

    def _current_sql(self, where: str = "") -> str:
        """Select the current row of each pair among this KB's rows matching `where`.

        The current row is the latest one, unless a reviewed row has the
        latest content hashes: then the latest reviewed row.
        """

        condition = "knowledge_base_id = :kb" + (f" AND {where}" if where else "")
        return (
            "SELECT DISTINCT ON (a_id, b_id) * FROM (SELECT *, first_value(a_hash) OVER w AS latest_a, "
            f"first_value(b_hash) OVER w AS latest_b FROM {_TABLE} WHERE {condition} "
            "WINDOW w AS (PARTITION BY a_id, b_id ORDER BY updated_at DESC, id DESC)) AS r "
            f"ORDER BY a_id, b_id, ({_REVIEWED_SQL} AND a_hash = latest_a AND b_hash = latest_b) DESC, "
            "updated_at DESC, id DESC"
        )

    async def _select(
        self, sql: str, params: dict[str, Any], session: AsyncSession | None = None,
    ) -> list[ConflictRow]:
        """Execute a conflict query scoped to this knowledge base."""

        async with self._transaction(session) as session:
            rows = (await session.execute(text(sql), {"kb": self.knowledge_base_id, **params})).mappings().all()
        return [_row(row) for row in rows]

    async def get(
        self, pairs: Iterable[tuple[UUID, UUID]], fingerprint: str
    ) -> dict[tuple[UUID, UUID], ConflictRow]:
        """Return the stored rows of ordered pairs for one judge fingerprint."""

        params = _pair_params(pairs)
        if not params["a_ids"]:
            return {}
        rows = await self._select(
            f"SELECT * FROM {_TABLE} WHERE knowledge_base_id = :kb "
            f"AND judge_fingerprint = :fingerprint AND {_PAIRS_SQL}",
            {**params, "fingerprint": fingerprint},
        )
        return {row.pair: row for row in rows}

    async def put(self, row: ConflictRow, *, session: AsyncSession | None = None) -> ConflictRow:
        """Store a judgement; a reviewer's decision on the same content is kept as is.

        Returns:
            The stored row: the new judgement, or the kept reviewed row.

        Raises:
            sqlalchemy.exc.IntegrityError: A chunk is not in the knowledge base.
        """

        values: dict[str, Any] = {
            column: getattr(row, column) for column in _INSERTED if column not in {"id", "knowledge_base_id"}
        }
        values.update(id=new_id(), knowledge_base_id=self.knowledge_base_id)
        values["job_id"] = UUID(row.job_id) if row.job_id else None
        for column in _JSON_COLUMNS:
            if column in values and values[column] is not None:
                values[column] = json_text(values[column])
        placeholders = ", ".join(
            f"CAST(:{column} AS jsonb)" if column in _JSON_COLUMNS else f":{column}" for column in _INSERTED
        )
        async with self._transaction(session) as session:
            stored = (await session.execute(text(
                f"INSERT INTO {_TABLE} ({', '.join(_INSERTED)}) VALUES ({placeholders}) "
                "ON CONFLICT (a_id, b_id, judge_fingerprint) DO UPDATE SET "
                # Writing knowledge_base_id makes a pair stored under another
                # knowledge base fail the database rules instead of being changed.
                + ", ".join(f"{column} = EXCLUDED.{column}" for column in ("knowledge_base_id", *_JUDGED))
                + f" WHERE {_TABLE}.status IN ({_MACHINE_SQL}) "
                f"OR {_TABLE}.a_hash <> EXCLUDED.a_hash OR {_TABLE}.b_hash <> EXCLUDED.b_hash "
                f"OR {_TABLE}.knowledge_base_id <> EXCLUDED.knowledge_base_id "
                "RETURNING *"
            ), values)).mappings().first()
            if stored is None:
                stored = (await session.execute(text(
                    f"SELECT * FROM {_TABLE} WHERE knowledge_base_id = :knowledge_base_id "
                    "AND a_id = :a_id AND b_id = :b_id AND judge_fingerprint = :judge_fingerprint"
                ), values)).mappings().one()
        return _row(stored)

    async def current(
        self, pairs: Iterable[tuple[UUID, UUID]], *, session: AsyncSession | None = None,
    ) -> dict[tuple[UUID, UUID], ConflictRow]:
        """Return the current row of each ordered pair, under any judge fingerprint."""

        params = _pair_params(pairs)
        if not params["a_ids"]:
            return {}
        rows = await self._select(self._current_sql(_PAIRS_SQL), params, session)
        return {row.pair: row for row in rows}

    async def get_row(self, conflict_id: UUID) -> ConflictRow | None:
        """Return one row of this knowledge base by ID."""

        rows = await self._select(
            f"SELECT * FROM {_TABLE} WHERE knowledge_base_id = :kb AND id = :id", {"id": conflict_id}
        )
        return rows[0] if rows else None

    async def list_rows(
        self,
        *,
        statuses: Sequence[str] = (),
        record_ids: Sequence[UUID] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ConflictRow], dict[str, int]]:
        """Return one page of current rows, newest first, and the count per status.

        Args:
            statuses: Keep only rows in these statuses; all when empty.
            record_ids: Keep only pairs involving any of these chunks.
            limit: Page size.
            offset: Rows to skip.

        Returns:
            The page and the counts per status of the same selection, before
            the status filter.
        """

        params: dict[str, Any] = {"limit": limit, "offset": max(0, offset)}
        where = ""
        if record_ids:
            where = _RECORDS_SQL
            params["ids"] = list(record_ids)
        pairs = self._current_sql(where)
        status_filter = ""
        if statuses:
            status_filter = "WHERE status = ANY(CAST(:statuses AS text[]))"
            params["statuses"] = list(statuses)
        async with self._transaction() as session:
            bound = {"kb": self.knowledge_base_id, **params}
            rows = (await session.execute(text(
                f"SELECT * FROM ({pairs}) AS p {status_filter} ORDER BY updated_at DESC, id DESC "
                "LIMIT :limit OFFSET :offset"
            ), bound)).mappings().all()
            counted = (await session.execute(text(
                f"SELECT status, count(*) AS n FROM ({pairs}) AS p GROUP BY status"
            ), bound)).mappings().all()
        return [_row(row) for row in rows], {row["status"]: int(row["n"]) for row in counted}

    async def for_records(
        self, record_ids: Sequence[UUID], statuses: Sequence[str]
    ) -> list[ConflictRow]:
        """Return current rows of pairs involving these chunks, newest first.

        Args:
            record_ids: Chunks whose pairs are read.
            statuses: Keep only rows in these statuses; all when empty.
        """

        if not record_ids:
            return []
        params: dict[str, Any] = {"ids": list(record_ids)}
        status_filter = ""
        if statuses:
            status_filter = "WHERE status = ANY(CAST(:statuses AS text[]))"
            params["statuses"] = list(statuses)
        return await self._select(
            f"SELECT * FROM ({self._current_sql(_RECORDS_SQL)}) AS p {status_filter} "
            "ORDER BY updated_at DESC, id DESC",
            params,
        )

    async def set_review(
        self, conflict_id: UUID, status: str, *, linked: bool, event: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ConflictRow | None:
        """Store a reviewer's decision and append it to the row's `review` history.

        Returns:
            The updated row, or None when the row is not in this knowledge base.

        Raises:
            ValueError: The status is unknown.
        """

        if status not in STATUSES:
            raise ValueError(f"Unknown conflict status: {status!r}")
        rows = await self._select(
            f"UPDATE {_TABLE} SET status = :status, linked = :linked, "
            "review = review || CAST(:event AS jsonb) "
            "WHERE knowledge_base_id = :kb AND id = :id RETURNING *",
            {"id": conflict_id, "status": status, "linked": linked, "event": json_text([event])},
            session,
        )
        return rows[0] if rows else None
