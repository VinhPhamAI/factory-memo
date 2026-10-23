"""Persistent stage jobs for source processing: PostgreSQL notify-consume queue.

A source moves raw -> converted -> extracted, then (knowledge mode, when the KB
enables it) its new records are checked for conflicts. Each step is one job row
for one stage (`convert`, `extract` or `conflict`) in the DATABASE_URL database, shared by all
knowledge bases like the KB registry. A job may name the stages that follow it
(`next_stages`); finishing the job and creating the next one happen in a single
transaction, so a pipeline never stops between stages or runs a stage twice.

Notify-consume: enqueueing inserts the row and calls `pg_notify` on the
stage's channel in the same transaction (delivered on commit). Workers LISTEN
on their stage's channel, then claim with `FOR UPDATE SKIP LOCKED`, so any
number of worker processes can share a stage. A notification is only a wake-up
hint; workers also poll, so a missed notification delays a job but never loses it.

A running job keeps a heartbeat. A job whose worker stopped heartbeating (a
crash or a killed container) is requeued, or failed after `max_attempts`.
At most one job per source is queued or running (a unique partial index).
`options` carry the reviewer's request to every stage of a pipeline (today
only `confirm_overlap`: extract despite a near-duplicate warning).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.src.storage.databases import server_url_from_env
from backend.src.storage.knowledge_store import StorageError


logger = logging.getLogger(__name__)

JOBS_TABLE = "ingestion_jobs"
STAGES = ("convert", "extract", "conflict")
SOURCE_KINDS = ("upload", "conversation")
MODES = ("knowledge", "documents")
ACTIVE = ("queued", "running")
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_KB_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


class JobConflict(StorageError):
    """The source already has a queued or running job."""


class JobLeaseLost(StorageError):
    """The job is no longer running for this worker (it was recovered or finished)."""


def _env_number(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def max_attempts_from_env() -> int:
    return max(1, int(_env_number("JOB_MAX_ATTEMPTS", 3)))


def retry_delay_from_env() -> float:
    return max(0.0, _env_number("JOB_RETRY_DELAY_S", 30))


@dataclass(frozen=True)
class Job:
    id: UUID
    kb: str
    stage: str
    source_kind: str
    source_id: str
    mode: str | None
    next_stages: tuple[str, ...]
    status: str
    attempts: int
    max_attempts: int
    created_at: datetime
    run_after: datetime
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    parent_id: UUID | None = None
    options: dict[str, Any] = field(default_factory=dict)
    locked_by: str | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    @property
    def source(self) -> tuple[str, str]:
        return self.source_kind, self.source_id

    def to_json(self) -> dict[str, Any]:
        """Public status; worker identities stay in the table and logs."""

        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.id), "stage": self.stage, "status": self.status, "mode": self.mode,
            "next_stages": list(self.next_stages), "attempts": self.attempts,
            "confirm_overlap": bool(self.options.get("confirm_overlap")),
            "max_attempts": self.max_attempts, "error": self.error,
            "created_at": iso(self.created_at), "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "retry_at": iso(self.run_after) if self.status == "queued" and self.attempts else None,
        }


def _checked_job(kb: str, stage: str, source_kind: str, mode: str | None,
                 next_stages: Sequence[str]) -> tuple[str, ...]:
    if not _KB_RE.match(kb):
        raise ValueError(f"Invalid knowledge base name: {kb!r}")
    if stage not in STAGES or any(item not in STAGES for item in next_stages):
        raise ValueError(f"Unknown stage; expected one of {STAGES}")
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"Unknown source kind: {source_kind!r}")
    if mode is not None and mode not in MODES:
        raise ValueError(f"Unknown extraction mode: {mode!r}")
    return tuple(next_stages)


def retry_delay(attempts: int, base: float) -> float:
    """Exponential backoff after a failed attempt, capped at one hour."""

    return min(3600.0, base * 2 ** max(0, attempts - 1))


# ------------------------------------------------------------------ PostgreSQL


class JobQueue:
    """Job rows in the DATABASE_URL database; the engine opens on first use."""

    def __init__(
        self,
        database_url: str | Callable[[], str] = server_url_from_env,
        *,
        schema: str = "public",
        max_attempts: int | None = None,
        retry_delay_s: float | None = None,
    ) -> None:
        if not _NAME_RE.match(schema):
            raise ValueError(f"Invalid schema name: {schema!r}")
        self._url = database_url
        self._schema = schema
        self._table = f'"{schema}".{JOBS_TABLE}'
        # Channels are database-wide; a test schema gets its own channels.
        self._prefix = JOBS_TABLE if schema == "public" else f"{schema}_jobs"[:50]
        self.max_attempts = max_attempts or max_attempts_from_env()
        self.retry_delay_s = retry_delay_from_env() if retry_delay_s is None else retry_delay_s
        self._engine: AsyncEngine | None = None
        self._ready = False
        self._ready_lock = asyncio.Lock()

    def channel(self, stage: str) -> str:
        return f"{self._prefix}_{stage}"

    def _database_url(self) -> str:
        try:
            return self._url() if callable(self._url) else self._url
        except ValueError as exc:
            raise StorageError(str(exc)) from exc

    async def _ensure(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(self._database_url(), pool_pre_ping=True)
        if self._ready:
            return self._engine
        async with self._ready_lock:
            if self._ready:
                return self._engine
            name = f"{self._schema}_{JOBS_TABLE}"[:50]
            try:
                async with self._engine.begin() as conn:
                    # API and worker processes may start together; create once.
                    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                                       {"key": self._table})
                    await conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self._table} (
                            id uuid PRIMARY KEY,
                            kb text NOT NULL,
                            stage text NOT NULL,
                            source_kind text NOT NULL,
                            source_id text NOT NULL,
                            mode text,
                            next_stages jsonb NOT NULL DEFAULT '[]'::jsonb,
                            status text NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'running', 'done', 'failed')),
                            attempts integer NOT NULL DEFAULT 0,
                            max_attempts integer NOT NULL DEFAULT 3,
                            run_after timestamptz NOT NULL DEFAULT now(),
                            error text,
                            result jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                            parent_id uuid,
                            locked_by text,
                            created_at timestamptz NOT NULL DEFAULT now(),
                            started_at timestamptz,
                            heartbeat_at timestamptz,
                            finished_at timestamptz,
                            updated_at timestamptz NOT NULL DEFAULT now()
                        )
                    """))
                    await conn.execute(text(
                        f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS "
                        "options jsonb NOT NULL DEFAULT '{}'::jsonb"
                    ))
                    await conn.execute(text(
                        f"CREATE UNIQUE INDEX IF NOT EXISTS {name}_active ON {self._table} "
                        "(kb, source_kind, source_id) WHERE status IN ('queued', 'running')"
                    ))
                    await conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {name}_claim ON {self._table} "
                        "(stage, run_after, created_at) WHERE status = 'queued'"
                    ))
                    await conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {name}_source ON {self._table} "
                        "(kb, source_kind, source_id, created_at DESC)"
                    ))
            except SQLAlchemyError as exc:
                raise StorageError(f"Could not prepare the job queue: {exc}") from exc
            self._ready = True
        return self._engine

    @staticmethod
    def _job(row: Any) -> Job:
        values = dict(row)
        values["next_stages"] = tuple(values.get("next_stages") or ())
        values["result"] = values.get("result") or {}
        values["options"] = values.get("options") or {}
        values.pop("updated_at", None)
        return Job(**values)

    async def _notify(self, conn, stage: str, job_id: UUID) -> None:
        await conn.execute(text("SELECT pg_notify(:channel, :id)"),
                           {"channel": self.channel(stage), "id": str(job_id)})

    async def _insert(self, conn, *, kb: str, stage: str, source_kind: str, source_id: str,
                      mode: str | None, next_stages: Sequence[str],
                      parent_id: UUID | None = None, options: dict[str, Any] | None = None) -> Job:
        row = (await conn.execute(text(
            f"INSERT INTO {self._table} (id, kb, stage, source_kind, source_id, mode, "
            "next_stages, max_attempts, parent_id, options) VALUES (:id, :kb, :stage, :source_kind, "
            ":source_id, :mode, CAST(:next_stages AS jsonb), :max_attempts, :parent_id, "
            "CAST(:options AS jsonb)) RETURNING *"
        ), {
            "id": uuid4(), "kb": kb, "stage": stage, "source_kind": source_kind,
            "source_id": source_id, "mode": mode, "next_stages": json.dumps(list(next_stages)),
            "max_attempts": self.max_attempts, "parent_id": parent_id,
            "options": json.dumps(options or {}),
        })).mappings().one()
        await self._notify(conn, stage, row["id"])
        return self._job(row)

    async def enqueue(self, kb: str, stage: str, source_kind: str, source_id: str, *,
                      mode: str | None = None, next_stages: Sequence[str] = (),
                      options: dict[str, Any] | None = None) -> Job:
        next_stages = _checked_job(kb, stage, source_kind, mode, next_stages)
        engine = await self._ensure()
        try:
            async with engine.begin() as conn:
                return await self._insert(conn, kb=kb, stage=stage, source_kind=source_kind,
                                          source_id=source_id, mode=mode, next_stages=next_stages,
                                          options=options)
        except IntegrityError as exc:
            raise JobConflict("This source already has a queued or running job") from exc
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not enqueue the job: {exc}") from exc

    async def claim(self, stage: str, worker: str) -> Job | None:
        engine = await self._ensure()
        try:
            async with engine.begin() as conn:
                row = (await conn.execute(text(
                    f"UPDATE {self._table} SET status = 'running', attempts = attempts + 1, "
                    "locked_by = :worker, started_at = now(), heartbeat_at = now(), "
                    "updated_at = now(), error = NULL "
                    f"WHERE id = (SELECT id FROM {self._table} WHERE stage = :stage "
                    "AND status = 'queued' AND run_after <= now() ORDER BY run_after, created_at "
                    "FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *"
                ), {"stage": stage, "worker": worker})).mappings().first()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not claim a job: {exc}") from exc
        return self._job(row) if row else None

    async def _owned(self, conn, job: Job, worker: str, assignments: str,
                     parameters: dict[str, Any] | None = None) -> Any:
        row = (await conn.execute(text(
            f"UPDATE {self._table} SET {assignments}, updated_at = now() "
            "WHERE id = :id AND status = 'running' AND locked_by = :worker RETURNING *"
        ), {"id": job.id, "worker": worker, **(parameters or {})})).mappings().first()
        if row is None:
            raise JobLeaseLost(f"Job {job.id} is no longer owned by {worker}")
        return row

    async def heartbeat(self, job: Job, worker: str) -> bool:
        engine = await self._ensure()
        try:
            async with engine.begin() as conn:
                await self._owned(conn, job, worker, "heartbeat_at = now()")
        except JobLeaseLost:
            return False
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not record a heartbeat: {exc}") from exc
        return True

    async def complete(self, job: Job, worker: str, result: dict[str, Any] | None = None) -> Job | None:
        """Marks the job done and, in the same transaction, queues its next stage."""

        engine = await self._ensure()
        try:
            async with engine.begin() as conn:
                await self._owned(
                    conn, job, worker,
                    "status = 'done', finished_at = now(), locked_by = NULL, "
                    "result = CAST(:result AS jsonb)",
                    {"result": json.dumps(result or {}, ensure_ascii=False, default=str)},
                )
                if not job.next_stages:
                    return None
                return await self._insert(
                    conn, kb=job.kb, stage=job.next_stages[0], source_kind=job.source_kind,
                    source_id=job.source_id, mode=job.mode, next_stages=job.next_stages[1:],
                    parent_id=job.id, options=job.options,
                )
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not complete job {job.id}: {exc}") from exc

    async def fail(self, job: Job, worker: str, error: str, *, retry: bool) -> Job:
        """Requeues with backoff while attempts remain and `retry`; otherwise failed."""

        requeue = retry and job.attempts < job.max_attempts
        engine = await self._ensure()
        try:
            async with engine.begin() as conn:
                row = await self._owned(
                    conn, job, worker,
                    "status = :status, error = :error, locked_by = NULL, "
                    "run_after = now() + make_interval(secs => :delay), "
                    "finished_at = CASE WHEN :status = 'failed' THEN now() END",
                    {"status": "queued" if requeue else "failed", "error": error[:2000],
                     "delay": retry_delay(job.attempts, self.retry_delay_s)},
                )
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not record the failure of job {job.id}: {exc}") from exc
        return self._job(row)

    async def release(self, job: Job, worker: str) -> None:
        """Hands an unfinished job back (worker shutdown); the attempt is not counted."""

        engine = await self._ensure()
        try:
            async with engine.begin() as conn:
                await self._owned(conn, job, worker,
                                  "status = 'queued', attempts = GREATEST(attempts - 1, 0), "
                                  "locked_by = NULL, run_after = now()")
                await self._notify(conn, job.stage, job.id)
        except JobLeaseLost:
            pass
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not release job {job.id}: {exc}") from exc

    async def recover_stale(self, older_than_s: float) -> list[Job]:
        """Requeues (or fails) running jobs whose worker stopped heartbeating."""

        engine = await self._ensure()
        try:
            async with engine.begin() as conn:
                rows = (await conn.execute(text(
                    f"UPDATE {self._table} SET "
                    "status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END, "
                    "finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END, "
                    "error = :error, locked_by = NULL, run_after = now(), updated_at = now() "
                    "WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => :age) "
                    "RETURNING *"
                ), {"age": older_than_s, "error": STALE_ERROR})).mappings().all()
                for row in rows:
                    if row["status"] == "queued":
                        await self._notify(conn, row["stage"], row["id"])
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not recover stale jobs: {exc}") from exc
        return [self._job(row) for row in rows]

    async def latest(self, kb: str, sources: Iterable[tuple[str, str]] | None = None) -> dict[tuple[str, str], Job]:
        """The newest job of each source in a KB (optionally only the given sources)."""

        engine = await self._ensure()
        selected = list(sources) if sources is not None else None
        if selected == []:
            return {}
        where, parameters = "kb = :kb", {"kb": kb}
        if selected is not None:
            where += " AND (source_kind || ':' || source_id) = ANY(:keys)"
            parameters["keys"] = [f"{kind}:{source_id}" for kind, source_id in selected]
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text(
                    f"SELECT DISTINCT ON (source_kind, source_id) * FROM {self._table} "
                    f"WHERE {where} ORDER BY source_kind, source_id, created_at DESC"
                ), parameters)).mappings().all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read jobs: {exc}") from exc
        return {(row["source_kind"], row["source_id"]): self._job(row) for row in rows}

    def listener(self, stages: Sequence[str]) -> "PgListener":
        url = make_url(self._database_url()).set(drivername="postgresql")
        return PgListener(url.render_as_string(hide_password=False),
                          [self.channel(stage) for stage in stages])

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._ready = False


STALE_ERROR = "Worker dừng giữa chừng (mất heartbeat); job được xếp hàng lại."


class PgListener:
    """A dedicated autocommit connection that LISTENs on the stage channels."""

    def __init__(self, conninfo: str, channels: Sequence[str]) -> None:
        self._conninfo = conninfo
        self._channels = list(channels)
        self._conn: Any = None

    async def _connect(self) -> None:
        import psycopg
        from psycopg import sql

        self._conn = await psycopg.AsyncConnection.connect(self._conninfo, autocommit=True)
        for channel in self._channels:
            await self._conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))

    async def wait(self, timeout: float) -> bool:
        """True when a notification arrived, False after `timeout` seconds."""

        import psycopg

        try:
            if self._conn is None or self._conn.closed:
                await self._connect()
            async for _ in self._conn.notifies(timeout=timeout, stop_after=1):
                return True
            return False
        except (psycopg.Error, OSError):
            # Polling still finds the jobs; reconnect on the next wait.
            logger.warning("Job notification connection lost; polling until it reconnects",
                           exc_info=True)
            await self.aclose()
            await asyncio.sleep(min(timeout, 5))
            return False

    async def aclose(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None


# ------------------------------------------------------------------ in memory


class MemoryJobQueue:
    """The same contract within one process: offline tests and local experiments."""

    def __init__(self, *, max_attempts: int = 3, retry_delay_s: float = 0.0) -> None:
        self.max_attempts = max_attempts
        self.retry_delay_s = retry_delay_s
        self.jobs: dict[UUID, Job] = {}
        self._listeners: list[MemoryListener] = []

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _notify(self, stage: str) -> None:
        for listener in self._listeners:
            if stage in listener.stages:
                listener.event.set()

    def _insert(self, *, kb, stage, source_kind, source_id, mode, next_stages, parent_id=None,
                options=None) -> Job:
        if any(job.active and job.kb == kb and job.source == (source_kind, source_id)
               for job in self.jobs.values()):
            raise JobConflict("This source already has a queued or running job")
        now = self._now()
        job = Job(id=uuid4(), kb=kb, stage=stage, source_kind=source_kind, source_id=source_id,
                  mode=mode, next_stages=tuple(next_stages), status="queued", attempts=0,
                  max_attempts=self.max_attempts, created_at=now, run_after=now, parent_id=parent_id,
                  options=dict(options or {}))
        self.jobs[job.id] = job
        self._notify(stage)
        return job

    async def enqueue(self, kb, stage, source_kind, source_id, *, mode=None, next_stages=(),
                      options=None) -> Job:
        next_stages = _checked_job(kb, stage, source_kind, mode, next_stages)
        return self._insert(kb=kb, stage=stage, source_kind=source_kind, source_id=source_id,
                            mode=mode, next_stages=next_stages, options=options)

    async def claim(self, stage, worker) -> Job | None:
        now = self._now()
        ready = sorted((job for job in self.jobs.values()
                        if job.stage == stage and job.status == "queued" and job.run_after <= now),
                       key=lambda job: (job.run_after, job.created_at))
        if not ready:
            return None
        job = replace(ready[0], status="running", attempts=ready[0].attempts + 1, locked_by=worker,
                      started_at=now, heartbeat_at=now, error=None)
        self.jobs[job.id] = job
        return job

    def _owned(self, job: Job, worker: str) -> Job:
        current = self.jobs.get(job.id)
        if current is None or current.status != "running" or current.locked_by != worker:
            raise JobLeaseLost(f"Job {job.id} is no longer owned by {worker}")
        return current

    async def heartbeat(self, job, worker) -> bool:
        try:
            current = self._owned(job, worker)
        except JobLeaseLost:
            return False
        self.jobs[job.id] = replace(current, heartbeat_at=self._now())
        return True

    async def complete(self, job, worker, result=None) -> Job | None:
        current = self._owned(job, worker)
        self.jobs[job.id] = replace(current, status="done", finished_at=self._now(),
                                    locked_by=None, result=dict(result or {}))
        if not current.next_stages:
            return None
        return self._insert(kb=current.kb, stage=current.next_stages[0],
                            source_kind=current.source_kind, source_id=current.source_id,
                            mode=current.mode, next_stages=current.next_stages[1:],
                            parent_id=current.id, options=current.options)

    async def fail(self, job, worker, error, *, retry) -> Job:
        current = self._owned(job, worker)
        requeue = retry and current.attempts < current.max_attempts
        now = self._now()
        updated = replace(
            current, status="queued" if requeue else "failed", error=error[:2000], locked_by=None,
            run_after=now + timedelta(seconds=retry_delay(current.attempts, self.retry_delay_s)),
            finished_at=None if requeue else now,
        )
        self.jobs[job.id] = updated
        return updated

    async def release(self, job, worker) -> None:
        try:
            current = self._owned(job, worker)
        except JobLeaseLost:
            return
        self.jobs[job.id] = replace(current, status="queued", attempts=max(current.attempts - 1, 0),
                                    locked_by=None, run_after=self._now())
        self._notify(current.stage)

    async def recover_stale(self, older_than_s) -> list[Job]:
        limit = self._now() - timedelta(seconds=older_than_s)
        recovered = []
        for job in list(self.jobs.values()):
            if job.status == "running" and job.heartbeat_at and job.heartbeat_at < limit:
                requeue = job.attempts < job.max_attempts
                updated = replace(job, status="queued" if requeue else "failed", error=STALE_ERROR,
                                  locked_by=None, run_after=self._now(),
                                  finished_at=None if requeue else self._now())
                self.jobs[job.id] = updated
                recovered.append(updated)
                if requeue:
                    self._notify(job.stage)
        return recovered

    async def latest(self, kb, sources=None) -> dict[tuple[str, str], Job]:
        wanted = set(sources) if sources is not None else None
        latest: dict[tuple[str, str], Job] = {}
        for job in sorted(self.jobs.values(), key=lambda job: job.created_at):
            if job.kb == kb and (wanted is None or job.source in wanted):
                latest[job.source] = job
        return latest

    def listener(self, stages) -> "MemoryListener":
        listener = MemoryListener(self, stages)
        self._listeners.append(listener)
        return listener

    async def aclose(self) -> None:
        pass


class MemoryListener:
    def __init__(self, queue: MemoryJobQueue, stages: Sequence[str]) -> None:
        self._queue = queue
        self.stages = set(stages)
        self.event = asyncio.Event()

    async def wait(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self.event.wait(), timeout)
        except TimeoutError:
            return False
        self.event.clear()
        return True

    async def aclose(self) -> None:
        if self in self._queue._listeners:
            self._queue._listeners.remove(self)
