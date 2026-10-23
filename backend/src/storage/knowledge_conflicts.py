"""Judged pairs of knowledge records: conflict cache, review queue and evaluation data.

One row per ordered record pair (`a_id < b_id`) and judge fingerprint (prompt,
model, judge mode) in the KB's own database, next to `knowledge_items`. A row
keeps the hashes of both records' content when they were judged; a pair whose
content changed, or whose verdict is `pending`, is judged again. The verdicts
of each step (listwise, pairwise confirmation) are kept next to the final one.

Statuses: `no_conflict` (the judge found no conflict), `open` (a suspected
conflict waiting for review), `pending` (no valid verdict yet), `needs_review`
(the verdict's quotes did not match the records), and the reviewer's
`confirmed`, `dismissed` and `resolved`. A reviewer's decision holds for
the content it saw, under any judge fingerprint: the pair is not judged
again until either record's content changes. Then a new judgement replaces
it (the row's `review` history is kept). `linked` says whether the conflict
links are on the records (`conflict_with` / `contradicted_by`); both records
stay drafts either way. `review` keeps the reviewer's decisions, oldest first.

The current row of a pair (what listings show) is its latest row, unless a
reviewed row has the same content hashes: then the latest reviewed row.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.src.storage.knowledge_store import StorageError


CONFLICTS_TABLE = "knowledge_conflicts"
MACHINE_STATUSES = ("no_conflict", "open", "pending", "needs_review")
REVIEW_STATUSES = ("confirmed", "dismissed", "resolved")
STATUSES = MACHINE_STATUSES + REVIEW_STATUSES


def ordered(first: UUID, second: UUID) -> tuple[UUID, UUID]:
    return (first, second) if str(first) < str(second) else (second, first)


@dataclass(frozen=True)
class ConflictRow:
    a_id: UUID
    b_id: UUID
    a_hash: str
    b_hash: str
    judge_fingerprint: str
    status: str
    # The record whose extraction led to the judgement.
    new_id: UUID | None = None
    relation: str | None = None
    aspect: str | None = None
    confidence: float | None = None
    reason: str | None = None
    quotes: dict[str, Any] = field(default_factory=dict)
    list_verdict: dict[str, Any] | None = None
    pair_verdict: dict[str, Any] | None = None
    linked: bool = False
    job_id: str | None = None
    review: list[dict[str, Any]] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"Unknown conflict status: {self.status!r}")
        if ordered(self.a_id, self.b_id) != (self.a_id, self.b_id):
            raise ValueError("Conflict pairs are stored in order (a_id < b_id)")

    @property
    def pair(self) -> tuple[UUID, UUID]:
        return self.a_id, self.b_id

    @property
    def reviewed(self) -> bool:
        return self.status in REVIEW_STATUSES

    def to_json(self) -> dict[str, Any]:
        return {
            "id": str(self.id), "a_id": str(self.a_id), "b_id": str(self.b_id),
            "new_id": str(self.new_id) if self.new_id else None, "status": self.status,
            "relation": self.relation, "aspect": self.aspect, "confidence": self.confidence,
            "reason": self.reason, "quotes": self.quotes, "linked": self.linked,
            "confirmed_by_pair": (self.pair_verdict or {}).get("relation") in {"contradiction", "case_exception"},
            "judge_fingerprint": self.judge_fingerprint, "job_id": self.job_id, "review": self.review,
            "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat(),
        }


_COLUMNS = ("id", "a_id", "b_id", "a_hash", "b_hash", "judge_fingerprint", "new_id", "relation",
            "aspect", "confidence", "reason", "quotes", "list_verdict", "pair_verdict", "status",
            "linked", "job_id", "review", "created_at", "updated_at")
_JSON_COLUMNS = ("quotes", "list_verdict", "pair_verdict", "review")
_REVIEWED_SQL = f"status IN ({', '.join(repr(s) for s in REVIEW_STATUSES)})"


def _newest(rows: Iterable[ConflictRow]) -> list[ConflictRow]:
    return sorted(rows, key=lambda row: (row.updated_at, str(row.id)), reverse=True)


def current(rows: Iterable[ConflictRow]) -> list[ConflictRow]:
    """The current row of each pair (see the module docstring), newest first."""

    by_pair: dict[tuple[UUID, UUID], list[ConflictRow]] = {}
    for row in _newest(rows):
        by_pair.setdefault(row.pair, []).append(row)
    chosen = []
    for group in by_pair.values():
        latest = group[0]
        reviewed = [row for row in group if row.reviewed and (row.a_hash, row.b_hash) == (latest.a_hash, latest.b_hash)]
        chosen.append(reviewed[0] if reviewed else latest)
    return _newest(chosen)


class ConflictStore:
    """Conflict rows in a KB database (shares the knowledge store's engine)."""

    def __init__(self, engine: AsyncEngine, *, schema: str = "public") -> None:
        self._engine = engine
        self._schema = schema
        self._table = f'"{schema}".{CONFLICTS_TABLE}'

    async def init_schema(self) -> None:
        name = f"{self._schema}_{CONFLICTS_TABLE}"[:50]
        statuses = ", ".join(f"'{status}'" for status in STATUSES)
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": self._table})
                await conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        id uuid PRIMARY KEY,
                        a_id uuid NOT NULL,
                        b_id uuid NOT NULL,
                        a_hash text NOT NULL,
                        b_hash text NOT NULL,
                        judge_fingerprint text NOT NULL,
                        new_id uuid,
                        relation text,
                        aspect text,
                        confidence double precision,
                        reason text,
                        quotes jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                        list_verdict jsonb,
                        pair_verdict jsonb,
                        status text NOT NULL CHECK (status IN ({statuses})),
                        linked boolean NOT NULL DEFAULT false,
                        job_id text,
                        review jsonb NOT NULL DEFAULT '[]'::jsonb,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        CHECK (a_id::text < b_id::text),
                        UNIQUE (a_id, b_id, judge_fingerprint)
                    )
                """))
                await conn.execute(text(
                    f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS review jsonb NOT NULL DEFAULT '[]'::jsonb"
                ))
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name}_b ON {self._table} (b_id)"))
                await conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {name}_status ON {self._table} (status, created_at DESC)"
                ))
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not prepare knowledge conflicts: {exc}") from exc

    @staticmethod
    def _row(row: Any) -> ConflictRow:
        values = {column: row[column] for column in _COLUMNS}
        values["quotes"] = values["quotes"] or {}
        values["review"] = values["review"] or []
        return ConflictRow(**values)

    @staticmethod
    def _keys(pairs: Iterable[tuple[UUID, UUID]]) -> list[str]:
        return [f"{a}:{b}" for a, b in dict.fromkeys(pairs)]

    def _current_sql(self, where: str = "") -> str:
        """The current row of each pair among the rows matching `where`."""

        return (
            "SELECT DISTINCT ON (a_id, b_id) * FROM (SELECT *, first_value(a_hash) OVER w AS latest_a, "
            f"first_value(b_hash) OVER w AS latest_b FROM {self._table} {where} "
            "WINDOW w AS (PARTITION BY a_id, b_id ORDER BY updated_at DESC, id DESC)) AS r "
            f"ORDER BY a_id, b_id, ({_REVIEWED_SQL} AND a_hash = latest_a AND b_hash = latest_b) DESC, "
            "updated_at DESC, id DESC"
        )

    async def get(self, pairs: Iterable[tuple[UUID, UUID]], fingerprint: str) -> dict[tuple[UUID, UUID], ConflictRow]:
        """Stored rows of the given (ordered) pairs for one judge fingerprint."""

        pairs = list(dict.fromkeys(pairs))
        if not pairs:
            return {}
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(text(
                    f"SELECT * FROM {self._table} WHERE judge_fingerprint = :fingerprint "
                    "AND (a_id::text || ':' || b_id::text) = ANY(:keys)"
                ), {"fingerprint": fingerprint, "keys": [f"{a}:{b}" for a, b in pairs]})).mappings().all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read knowledge conflicts: {exc}") from exc
        return {(row["a_id"], row["b_id"]): self._row(row) for row in rows}

    async def put(self, row: ConflictRow) -> ConflictRow:
        """Stores a judgement; a reviewer's decision on the same content is kept as is."""

        values = {column: getattr(row, column) for column in _COLUMNS}
        for column in _JSON_COLUMNS:
            values[column] = None if values[column] is None else json.dumps(values[column], ensure_ascii=False, default=str)
        updated = [column for column in _COLUMNS
                   if column not in {"id", "a_id", "b_id", "judge_fingerprint", "review", "created_at"}]
        casts = {column: f"CAST(:{column} AS jsonb)" for column in _JSON_COLUMNS}
        try:
            async with self._engine.begin() as conn:
                stored = (await conn.execute(text(
                    f"INSERT INTO {self._table} ({', '.join(_COLUMNS)}) VALUES "
                    f"({', '.join(casts.get(column, ':' + column) for column in _COLUMNS)}) "
                    "ON CONFLICT (a_id, b_id, judge_fingerprint) DO UPDATE SET "
                    + ", ".join(f"{column} = EXCLUDED.{column}" for column in updated)
                    + f" WHERE {self._table}.status IN ({', '.join(repr(s) for s in MACHINE_STATUSES)}) "
                    f"OR {self._table}.a_hash <> EXCLUDED.a_hash OR {self._table}.b_hash <> EXCLUDED.b_hash "
                    "RETURNING *"
                ), values)).mappings().first()
                if stored is None:
                    stored = (await conn.execute(text(
                        f"SELECT * FROM {self._table} WHERE a_id = :a_id AND b_id = :b_id "
                        "AND judge_fingerprint = :judge_fingerprint"
                    ), values)).mappings().one()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not store a knowledge conflict: {exc}") from exc
        return self._row(stored)

    async def current(self, pairs: Iterable[tuple[UUID, UUID]]) -> dict[tuple[UUID, UUID], ConflictRow]:
        """The current row of each pair, under any judge fingerprint."""

        keys = self._keys(pairs)
        if not keys:
            return {}
        rows = await self._select(
            self._current_sql("WHERE (a_id::text || ':' || b_id::text) = ANY(:keys)"), {"keys": keys},
        )
        return {row.pair: row for row in rows}

    async def get_row(self, conflict_id: UUID) -> ConflictRow | None:
        rows = await self._select(f"SELECT * FROM {self._table} WHERE id = :id", {"id": conflict_id})
        return rows[0] if rows else None

    async def list_rows(
        self, *, statuses: Sequence[str] = (), record_ids: Sequence[UUID] = (),
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[ConflictRow], dict[str, int]]:
        """The current row of each pair, newest first, and the count per status.

        `record_ids` keeps pairs involving any of these records; the counts
        cover every status of the same selection.
        """

        where, params = "", {"limit": limit, "offset": max(0, offset)}
        if record_ids:
            where = "WHERE a_id = ANY(:ids) OR b_id = ANY(:ids)"
            params["ids"] = list(record_ids)
        pairs = self._current_sql(where)
        status_filter = ""
        if statuses:
            status_filter = "WHERE status = ANY(:statuses)"
            params["statuses"] = list(statuses)
        rows = await self._select(
            f"SELECT * FROM ({pairs}) AS p {status_filter} ORDER BY updated_at DESC, id DESC "
            "LIMIT :limit OFFSET :offset", params,
        )
        try:
            async with self._engine.connect() as conn:
                counted = (await conn.execute(text(
                    f"SELECT status, count(*) AS n FROM ({pairs}) AS p GROUP BY status"
                ), params)).mappings().all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not count knowledge conflicts: {exc}") from exc
        return rows, {row["status"]: int(row["n"]) for row in counted}

    async def for_records(self, record_ids: Sequence[UUID], statuses: Sequence[str]) -> list[ConflictRow]:
        """Current rows of pairs involving these records, kept when in `statuses` (all if empty)."""

        if not record_ids:
            return []
        params: dict[str, Any] = {"ids": list(record_ids)}
        status_filter = ""
        if statuses:
            status_filter = "WHERE status = ANY(:statuses)"
            params["statuses"] = list(statuses)
        rows = await self._select(
            f"SELECT * FROM ({self._current_sql('WHERE a_id = ANY(:ids) OR b_id = ANY(:ids)')}) AS p {status_filter}",
            params,
        )
        return _newest(rows)

    async def set_review(
        self, conflict_id: UUID, status: str, *, linked: bool, event: dict[str, Any],
    ) -> ConflictRow | None:
        """Store a reviewer's decision (any status) and append it to `review`."""

        if status not in STATUSES:
            raise ValueError(f"Unknown conflict status: {status!r}")
        try:
            async with self._engine.begin() as conn:
                row = (await conn.execute(text(
                    f"UPDATE {self._table} SET status = :status, linked = :linked, updated_at = now(), "
                    "review = COALESCE(review, '[]'::jsonb) || CAST(:event AS jsonb) WHERE id = :id RETURNING *"
                ), {"id": conflict_id, "status": status, "linked": linked,
                    "event": json.dumps([event], ensure_ascii=False, default=str)})).mappings().first()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not review a knowledge conflict: {exc}") from exc
        return self._row(row) if row else None

    async def _select(self, sql: str, params: dict[str, Any]) -> list[ConflictRow]:
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(text(sql), params)).mappings().all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read knowledge conflicts: {exc}") from exc
        return [self._row(row) for row in rows]


class MemoryConflictStore:
    """The same contract in memory, for offline tests."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID, str], ConflictRow] = {}

    async def init_schema(self) -> None:
        pass

    async def get(self, pairs, fingerprint):
        found = {}
        for pair in pairs:
            row = self.rows.get((*pair, fingerprint))
            if row is not None:
                found[pair] = row
        return found

    async def put(self, row: ConflictRow) -> ConflictRow:
        key = (row.a_id, row.b_id, row.judge_fingerprint)
        current = self.rows.get(key)
        if current is not None:
            if current.reviewed and (current.a_hash, current.b_hash) == (row.a_hash, row.b_hash):
                return current
            row = replace(row, id=current.id, created_at=current.created_at, review=current.review,
                          updated_at=datetime.now(timezone.utc))
        self.rows[key] = row
        return row

    async def current(self, pairs):
        wanted = set(pairs)
        return {row.pair: row for row in current(self.rows.values()) if row.pair in wanted}

    async def get_row(self, conflict_id):
        return next((row for row in self.rows.values() if row.id == conflict_id), None)

    async def list_rows(self, *, statuses=(), record_ids=(), limit=50, offset=0):
        ids = set(record_ids)
        rows = [row for row in current(self.rows.values()) if not ids or ids & set(row.pair)]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        rows = [row for row in rows if not statuses or row.status in statuses]
        return rows[offset:offset + limit], counts

    async def for_records(self, record_ids, statuses):
        ids = set(record_ids)
        return [row for row in current(self.rows.values())
                if ids & set(row.pair) and (not statuses or row.status in statuses)]

    async def set_review(self, conflict_id, status, *, linked, event):
        if status not in STATUSES:
            raise ValueError(f"Unknown conflict status: {status!r}")
        for key, row in self.rows.items():
            if row.id == conflict_id:
                updated = replace(row, status=status, linked=linked, review=[*row.review, event],
                                  updated_at=datetime.now(timezone.utc))
                self.rows[key] = updated
                return updated
        return None
