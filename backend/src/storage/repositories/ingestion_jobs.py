"""Stage jobs of knowledge sources in the application database.

The same notify-consume queue as `storage/job_queue.py`, on the ORM
`ingestion_jobs` table: jobs reference `(knowledge_base_id, source_id)`
instead of a KB name and a source kind. Enqueueing calls `pg_notify` in the
inserting transaction; workers LISTEN and also poll, and claim with
`FOR UPDATE SKIP LOCKED`. Finishing a job and creating its next stage happen
in one transaction. The source `status` follows its jobs in the same
transactions: `processing` once claimed, `processed` after the last stage and
`failed` when a job fails without a retry.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, null, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.storage.job_queue import (
    STALE_ERROR,
    JobConflict,
    JobLeaseLost,
    PgListener,
    max_attempts_from_env,
    retry_delay,
    retry_delay_from_env,
)
from backend.src.storage.orm import (
    ExtractionMode,
    IngestionJob,
    JobStage,
    JobStatus,
    KnowledgeSource,
    SourceStatus,
    new_id,
)
from backend.src.storage.repositories.base import Repository, violated_constraint


ACTIVE_SOURCE_INDEX = "ix_ingestion_jobs_active_source"
CHANNEL_PREFIX = "ingestion_jobs"
MAX_ERROR_LENGTH = 2000
_JOBS = IngestionJob.__table__
_SOURCES = KnowledgeSource.__table__


@dataclass(frozen=True)
class Job:
    """One stage job; `to_json` is its public status."""

    id: UUID
    knowledge_base_id: UUID
    source_id: UUID
    stage: str
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
        return self.status in (JobStatus.QUEUED.value, JobStatus.RUNNING.value)

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
            "retry_at": (
                iso(self.run_after)
                if self.status == JobStatus.QUEUED.value and self.attempts else None
            ),
        }


def _job(row: Mapping[str, Any]) -> Job:
    values = {name: row[name] for name in Job.__dataclass_fields__}
    values["next_stages"] = tuple(values["next_stages"] or ())
    values["result"] = values["result"] or {}
    values["options"] = values["options"] or {}
    return Job(**values)


def _checked(stage: str, mode: str | None, next_stages: Sequence[str]) -> tuple[str, ...]:
    stages = {item.value for item in JobStage}
    if stage not in stages or any(item not in stages for item in next_stages):
        raise ValueError(f"Unknown stage; expected one of {sorted(stages)}")
    if mode is not None and mode not in {item.value for item in ExtractionMode}:
        raise ValueError(f"Unknown extraction mode: {mode!r}")
    return tuple(next_stages)


class IngestionJobRepository(Repository):
    """Queue of stage jobs shared by all knowledge bases and worker processes."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int | None = None,
        retry_delay_s: float | None = None,
        channel_prefix: str = CHANNEL_PREFIX,
    ) -> None:
        super().__init__(sessions)
        self.max_attempts = max_attempts or max_attempts_from_env()
        self.retry_delay_s = retry_delay_from_env() if retry_delay_s is None else retry_delay_s
        self._prefix = channel_prefix

    def channel(self, stage: str) -> str:
        """Notification channel of a stage."""

        return f"{self._prefix}_{stage}"

    async def _notify(self, session: AsyncSession, stage: str, job_id: UUID) -> None:
        await session.execute(
            text("SELECT pg_notify(:channel, :id)"),
            {"channel": self.channel(stage), "id": str(job_id)},
        )

    async def _insert(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: UUID,
        source_id: UUID,
        stage: str,
        mode: str | None,
        next_stages: Sequence[str],
        options: dict[str, Any] | None,
        parent_id: UUID | None = None,
    ) -> Job:
        row = (
            await session.execute(
                _JOBS.insert()
                .values(
                    id=new_id(),
                    knowledge_base_id=knowledge_base_id,
                    source_id=source_id,
                    stage=stage,
                    mode=mode,
                    next_stages=list(next_stages),
                    max_attempts=self.max_attempts,
                    parent_id=parent_id,
                    options=dict(options or {}),
                )
                .returning(*_JOBS.c)
            )
        ).mappings().one()
        await self._notify(session, stage, row["id"])
        return _job(row)

    async def _set_source_status(
        self, session: AsyncSession, job: Job | Mapping[str, Any], status: SourceStatus
    ) -> None:
        source_id = job["source_id"] if isinstance(job, Mapping) else job.source_id
        await session.execute(
            _SOURCES.update()
            .where(_SOURCES.c.id == source_id, _SOURCES.c.status != status.value)
            .values(status=status.value)
        )

    async def _owned(
        self, session: AsyncSession, job: Job, worker: str, values: dict[str, Any]
    ) -> Mapping[str, Any]:
        row = (
            await session.execute(
                _JOBS.update()
                .where(
                    _JOBS.c.id == job.id,
                    _JOBS.c.status == JobStatus.RUNNING.value,
                    _JOBS.c.locked_by == worker,
                )
                .values(**values)
                .returning(*_JOBS.c)
            )
        ).mappings().first()
        if row is None:
            raise JobLeaseLost(f"Job {job.id} is no longer owned by {worker}")
        return row

    async def enqueue(
        self,
        knowledge_base_id: UUID,
        source_id: UUID,
        stage: str,
        *,
        mode: str | None = None,
        next_stages: Sequence[str] = (),
        options: dict[str, Any] | None = None,
    ) -> Job:
        """Queue a stage job for a source and wake the stage's workers on commit.

        Args:
            knowledge_base_id: Knowledge base of the source.
            source_id: Source to process.
            stage: Stage to run first.
            mode: Extraction mode, for stages that extract.
            next_stages: Stages queued in order after this one completes.
            options: Reviewer requests carried to every stage.

        Returns:
            The queued job.

        Raises:
            ValueError: A stage or mode is unknown.
            JobConflict: The source already has a queued or running job.
            sqlalchemy.exc.IntegrityError: The source is not in the knowledge base.
            KnowledgeStorageUnavailable: The database could not be reached.
        """

        next_stages = _checked(stage, mode, next_stages)
        try:
            async with self._transaction() as session:
                return await self._insert(
                    session, knowledge_base_id=knowledge_base_id, source_id=source_id,
                    stage=stage, mode=mode, next_stages=next_stages, options=options,
                )
        except IntegrityError as exc:
            if violated_constraint(exc) == ACTIVE_SOURCE_INDEX:
                raise JobConflict("This source already has a queued or running job") from exc
            raise

    async def claim(self, stage: str, worker: str) -> Job | None:
        """Lease the oldest due job of a stage, or return None when none is due.

        Concurrent workers skip rows another worker is claiming, so each job
        goes to one worker. The source becomes `processing`.
        """

        due = (
            select(_JOBS.c.id)
            .where(
                _JOBS.c.stage == stage,
                _JOBS.c.status == JobStatus.QUEUED.value,
                _JOBS.c.run_after <= func.now(),
            )
            .order_by(_JOBS.c.run_after, _JOBS.c.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        async with self._transaction() as session:
            row = (
                await session.execute(
                    _JOBS.update()
                    .where(_JOBS.c.id == due)
                    .values(
                        status=JobStatus.RUNNING.value,
                        attempts=_JOBS.c.attempts + 1,
                        locked_by=worker,
                        started_at=func.now(),
                        heartbeat_at=func.now(),
                        error=None,
                    )
                    .returning(*_JOBS.c)
                )
            ).mappings().first()
            if row is not None:
                await self._set_source_status(session, row, SourceStatus.PROCESSING)
        return _job(row) if row else None

    async def heartbeat(self, job: Job, worker: str) -> bool:
        """Extend the worker's lease; False when the job is no longer its own."""

        try:
            async with self._transaction() as session:
                await self._owned(session, job, worker, {"heartbeat_at": func.now()})
        except JobLeaseLost:
            return False
        return True

    async def complete(
        self, job: Job, worker: str, result: dict[str, Any] | None = None
    ) -> Job | None:
        """Mark the job done and, in the same transaction, queue its next stage.

        Returns:
            The next stage job, or None after the last stage (the source
            becomes `processed`).

        Raises:
            JobLeaseLost: The job is no longer running for this worker.
        """

        async with self._transaction() as session:
            await self._owned(session, job, worker, {
                "status": JobStatus.DONE.value,
                "finished_at": func.now(),
                "locked_by": None,
                "result": dict(result or {}),
            })
            if not job.next_stages:
                await self._set_source_status(session, job, SourceStatus.PROCESSED)
                return None
            return await self._insert(
                session, knowledge_base_id=job.knowledge_base_id, source_id=job.source_id,
                stage=job.next_stages[0], mode=job.mode, next_stages=job.next_stages[1:],
                options=job.options, parent_id=job.id,
            )

    async def fail(self, job: Job, worker: str, error: str, *, retry: bool) -> Job:
        """Requeue with backoff while attempts remain and `retry`; otherwise fail.

        A requeued job leaves the source `processing`; a failed one marks it `failed`.

        Raises:
            JobLeaseLost: The job is no longer running for this worker.
        """

        requeue = retry and job.attempts < job.max_attempts
        delay = retry_delay(job.attempts, self.retry_delay_s)
        async with self._transaction() as session:
            row = await self._owned(session, job, worker, {
                "status": JobStatus.QUEUED.value if requeue else JobStatus.FAILED.value,
                "error": error[:MAX_ERROR_LENGTH],
                "locked_by": None,
                "run_after": func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay),
                "finished_at": None if requeue else func.now(),
            })
            if not requeue:
                await self._set_source_status(session, job, SourceStatus.FAILED)
        return _job(row)

    async def release(self, job: Job, worker: str) -> None:
        """Hand an unfinished job back at worker shutdown; the attempt is not counted."""

        try:
            async with self._transaction() as session:
                await self._owned(session, job, worker, {
                    "status": JobStatus.QUEUED.value,
                    "attempts": func.greatest(_JOBS.c.attempts - 1, 0),
                    "locked_by": None,
                    "run_after": func.now(),
                })
                await self._notify(session, job.stage, job.id)
        except JobLeaseLost:
            pass

    async def recover_stale(self, older_than_s: float) -> list[Job]:
        """Requeue, or fail after `max_attempts`, running jobs without a recent heartbeat."""

        retry = _JOBS.c.attempts < _JOBS.c.max_attempts
        async with self._transaction() as session:
            rows = (
                await session.execute(
                    _JOBS.update()
                    .where(
                        _JOBS.c.status == JobStatus.RUNNING.value,
                        _JOBS.c.heartbeat_at
                        < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, older_than_s),
                    )
                    .values(
                        status=case(
                            (retry, JobStatus.QUEUED.value), else_=JobStatus.FAILED.value
                        ),
                        finished_at=case((retry, null()), else_=func.now()),
                        error=STALE_ERROR,
                        locked_by=None,
                        run_after=func.now(),
                    )
                    .returning(*_JOBS.c)
                )
            ).mappings().all()
            for row in rows:
                if row["status"] == JobStatus.QUEUED.value:
                    await self._notify(session, row["stage"], row["id"])
                else:
                    await self._set_source_status(session, row, SourceStatus.FAILED)
        return [_job(row) for row in rows]

    async def latest(
        self, knowledge_base_id: UUID, source_ids: Iterable[UUID] | None = None
    ) -> dict[UUID, Job]:
        """Return the newest job of each source in a knowledge base.

        Args:
            knowledge_base_id: Knowledge base whose jobs are read.
            source_ids: Only these sources, or every source when None.

        Returns:
            Jobs keyed by source ID; sources without jobs are absent.
        """

        selected = list(source_ids) if source_ids is not None else None
        if selected == []:
            return {}
        query = (
            select(*_JOBS.c)
            .where(_JOBS.c.knowledge_base_id == knowledge_base_id)
            .ext(distinct_on(_JOBS.c.source_id))
            .order_by(_JOBS.c.source_id, _JOBS.c.created_at.desc(), _JOBS.c.id.desc())
        )
        if selected is not None:
            query = query.where(_JOBS.c.source_id.in_(selected))
        async with self._transaction() as session:
            rows = (await session.execute(query)).mappings().all()
        return {row["source_id"]: _job(row) for row in rows}

    def listener(self, database_url: str, stages: Sequence[str]) -> PgListener:
        """Return a LISTEN connection on the stages' channels, opened on its first wait.

        Args:
            database_url: SQLAlchemy URL of the application database.
            stages: Stages whose notifications wake the worker.
        """

        url = make_url(database_url).set(drivername="postgresql")
        return PgListener(
            url.render_as_string(hide_password=False),
            [self.channel(stage) for stage in stages],
        )
