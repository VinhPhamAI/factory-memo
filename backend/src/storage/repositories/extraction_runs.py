"""Extraction runs of knowledge sources in the application database.

The same rules as `storage/extraction_runs.py`, on the ORM `extraction_runs`
table and scoped to one knowledge base: a running or done run blocks
extracting the same normalized content in the same mode again within the
knowledge base. A unique partial index guarantees this for one extraction
fingerprint; a transaction lock on knowledge base, content and mode serializes
the check across fingerprints. Runs are history: finished, never deleted, and
their results are never overwritten.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import any_, func, literal, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.types import Text

from backend.src.storage.extraction_runs import ABANDONED, stale_after_from_env
from backend.src.storage.orm import (
    ContentSignature,
    ExtractionMode,
    ExtractionRun as ExtractionRunRow,
    RunStatus,
    new_id,
)
from backend.src.storage.repositories.base import Repository


MAX_ERROR_LENGTH = 2000
_RUNS = ExtractionRunRow.__table__
_SIGNATURES = ContentSignature.__table__
_ACTIVE = (RunStatus.RUNNING.value, RunStatus.DONE.value)


@dataclass(frozen=True)
class ExtractionRun:
    """One extraction attempt of a source."""

    id: UUID
    knowledge_base_id: UUID
    source_id: UUID
    content_sha256: str
    mode: str
    fingerprint: str
    status: str
    created_at: datetime
    updated_at: datetime
    original_sha256: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    job_id: UUID | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    finished_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status in _ACTIVE

    def to_json(self) -> dict[str, Any]:
        """Public summary; `evidence_id` keeps the legacy name of the source ID."""

        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.id), "evidence_id": str(self.source_id), "mode": self.mode,
            "status": self.status, "content_sha256": self.content_sha256,
            "fingerprint": self.fingerprint, "created_at": iso(self.created_at),
            "finished_at": iso(self.finished_at),
        }


class RunConflict(ValueError):
    """The content already has a running or done run of another source."""

    def __init__(self, run: ExtractionRun) -> None:
        super().__init__(f"Content already extracted by run {run.id}")
        self.run = run


def _run(row: Mapping[str, Any]) -> ExtractionRun:
    values = {name: row[name] for name in ExtractionRun.__dataclass_fields__}
    values["settings"] = values["settings"] or {}
    values["result"] = values["result"] or {}
    return ExtractionRun(**values)


def _checked(mode: str, status: str = RunStatus.RUNNING.value) -> None:
    if mode not in {item.value for item in ExtractionMode}:
        raise ValueError(f"Unknown extraction mode: {mode!r}")
    if status not in {item.value for item in RunStatus}:
        raise ValueError(f"Unknown run status: {status!r}")


class ExtractionRunRepository(Repository):
    """Extraction runs of the sources of one knowledge base."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        knowledge_base_id: UUID,
        *,
        stale_after_s: float | None = None,
    ) -> None:
        super().__init__(sessions)
        self.knowledge_base_id = knowledge_base_id
        self.stale_after_s = stale_after_from_env() if stale_after_s is None else stale_after_s

    def _live(self, runs: Any = _RUNS) -> Any:
        """Done runs, and running runs updated within `stale_after_s`.

        Stale running runs do not block: the next start marks them failed.
        """

        cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, self.stale_after_s)
        return (runs.c.status == RunStatus.DONE.value) | (
            (runs.c.status == RunStatus.RUNNING.value) & (runs.c.updated_at >= cutoff)
        )

    async def _first(self, *conditions: Any) -> ExtractionRun | None:
        query = (
            select(*_RUNS.c)
            .where(_RUNS.c.knowledge_base_id == self.knowledge_base_id, *conditions)
            .order_by((_RUNS.c.status == RunStatus.DONE.value).desc(), _RUNS.c.created_at)
            .limit(1)
        )
        async with self._transaction() as session:
            row = (await session.execute(query)).mappings().first()
        return _run(row) if row else None

    async def find_active(
        self, content: str, mode: str, *, exclude: UUID | None = None
    ) -> ExtractionRun | None:
        """Return a done or live running run of this content and mode, done runs first.

        Args:
            content: Normalized content hash (`content_sha256`).
            mode: Extraction mode.
            exclude: Source whose own runs are ignored.
        """

        conditions = [_RUNS.c.content_sha256 == content, _RUNS.c.mode == mode, self._live()]
        if exclude is not None:
            conditions.append(_RUNS.c.source_id != exclude)
        return await self._first(*conditions)

    async def find_active_original(self, original: str, mode: str) -> ExtractionRun | None:
        """Return a done or live running run of a source with the same exact text."""

        return await self._first(
            _RUNS.c.original_sha256 == original, _RUNS.c.mode == mode, self._live()
        )

    async def for_source(self, source_id: UUID) -> list[ExtractionRun]:
        """Return every run of a source, newest first."""

        query = (
            select(*_RUNS.c)
            .where(
                _RUNS.c.knowledge_base_id == self.knowledge_base_id,
                _RUNS.c.source_id == source_id,
            )
            .order_by(_RUNS.c.created_at.desc(), _RUNS.c.id.desc())
        )
        async with self._transaction() as session:
            rows = (await session.execute(query)).mappings().all()
        return [_run(row) for row in rows]

    async def start(
        self,
        *,
        source_id: UUID,
        content: str,
        mode: str,
        fingerprint: str,
        settings: dict[str, Any],
        original_sha256: str | None = None,
        job_id: UUID | None = None,
    ) -> ExtractionRun:
        """Start a run of a source, unless another source's run covers the content.

        A running run of the same source (a retried or requeued job) is taken
        over. A running run not updated for `stale_after_s` (its worker died)
        is marked failed first.

        Args:
            source_id: Source being extracted.
            content: Normalized content hash.
            mode: Extraction mode.
            fingerprint: Hash of the mode and its settings.
            settings: Settings behind the fingerprint, kept for audits.
            original_sha256: Hash of the exact text, when known.
            job_id: Job running the extraction.

        Returns:
            The running run.

        Raises:
            ValueError: The mode is unknown.
            RunConflict: Another source has a done or live running run of the content.
            KnowledgeStorageUnavailable: The database could not be reached.
        """

        _checked(mode)
        try:
            async with self._transaction() as session:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"extraction_runs:{self.knowledge_base_id}:{content}:{mode}"},
                )
                rows = (
                    await session.execute(
                        select(*_RUNS.c)
                        .where(
                            _RUNS.c.knowledge_base_id == self.knowledge_base_id,
                            _RUNS.c.content_sha256 == content,
                            _RUNS.c.mode == mode,
                            _RUNS.c.status.in_(_ACTIVE),
                        )
                        .order_by(
                            (_RUNS.c.status == RunStatus.DONE.value).desc(), _RUNS.c.created_at
                        )
                        .with_for_update()
                    )
                ).mappings().all()
                stale = datetime.now(timezone.utc) - timedelta(seconds=self.stale_after_s)
                for row in rows:
                    run = _run(row)
                    if run.status == RunStatus.RUNNING.value and run.source_id == source_id:
                        updated = (
                            await session.execute(
                                _RUNS.update()
                                .where(_RUNS.c.id == run.id)
                                .values(
                                    job_id=job_id,
                                    fingerprint=fingerprint,
                                    settings=dict(settings),
                                    updated_at=func.now(),
                                )
                                .returning(*_RUNS.c)
                            )
                        ).mappings().one()
                        return _run(updated)
                    if run.status == RunStatus.RUNNING.value and run.updated_at < stale:
                        await session.execute(
                            _RUNS.update()
                            .where(_RUNS.c.id == run.id)
                            .values(
                                status=RunStatus.FAILED.value,
                                error=ABANDONED,
                                finished_at=func.now(),
                            )
                        )
                        continue
                    raise RunConflict(run)
                row = (
                    await session.execute(
                        _RUNS.insert()
                        .values(
                            id=new_id(),
                            knowledge_base_id=self.knowledge_base_id,
                            source_id=source_id,
                            content_sha256=content,
                            original_sha256=original_sha256,
                            mode=mode,
                            fingerprint=fingerprint,
                            settings=dict(settings),
                            status=RunStatus.RUNNING.value,
                            job_id=job_id,
                        )
                        .returning(*_RUNS.c)
                    )
                ).mappings().one()
                return _run(row)
        except IntegrityError as exc:
            # Reachable only through a writer that skipped the lock; report what won.
            run = await self.find_active(content, mode)
            if run is None:
                raise
            raise RunConflict(run) from exc

    async def finish(
        self,
        run_id: UUID,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Finish a running run; a run that is no longer running is left as it is.

        Raises:
            ValueError: The status is unknown.
        """

        _checked(ExtractionMode.KNOWLEDGE.value, status)
        async with self._transaction() as session:
            await session.execute(
                _RUNS.update()
                .where(
                    _RUNS.c.id == run_id,
                    _RUNS.c.knowledge_base_id == self.knowledge_base_id,
                    _RUNS.c.status == RunStatus.RUNNING.value,
                )
                .values(
                    status=status,
                    result=dict(result or {}),
                    error=error and error[:MAX_ERROR_LENGTH],
                    finished_at=func.now(),
                )
            )

    async def overlap_candidates(
        self,
        bands: list[str],
        mode: str,
        *,
        version: str,
        content: str,
        exclude: UUID | None = None,
        limit: int = 20,
    ) -> list[tuple[ExtractionRun, int]]:
        """Return runs of sources sharing LSH bands with `bands`, most shared bands first.

        Sources with the same content (exact duplicates), `exclude` and other
        signature versions are left out. Each source comes with its done, or
        live running, run in `mode`; sources without one are skipped.

        Returns:
            `(run, shared band count)` pairs.
        """

        if not bands:
            return []
        wanted = literal(list(bands), ARRAY(Text))
        band = func.unnest(_SIGNATURES.c.bands).column_valued("band")
        hits = (
            select(func.count())
            .where(band == any_(wanted))
            .scalar_subquery()
        )
        signed = (
            select(_SIGNATURES.c.source_id, hits.label("hits"))
            .where(
                _SIGNATURES.c.knowledge_base_id == self.knowledge_base_id,
                _SIGNATURES.c.bands.op("&&")(wanted),
                _SIGNATURES.c.version == version,
                _SIGNATURES.c.content_sha256 != content,
            )
        )
        if exclude is not None:
            signed = signed.where(_SIGNATURES.c.source_id != exclude)
        signed = signed.subquery("signed")
        runs = _RUNS.alias("run")
        live = (
            select(*runs.c)
            .where(
                runs.c.knowledge_base_id == self.knowledge_base_id,
                runs.c.source_id == signed.c.source_id,
                runs.c.mode == mode,
                self._live(runs),
            )
            .order_by((runs.c.status == RunStatus.DONE.value).desc(), runs.c.created_at)
            .limit(1)
            .lateral("live")
        )
        query = (
            select(*live.c, signed.c.hits)
            .select_from(signed.join(live, literal(True)))
            .order_by(signed.c.hits.desc(), live.c.created_at)
            .limit(limit)
        )
        async with self._transaction() as session:
            rows = (await session.execute(query)).mappings().all()
        return [(_run(row), int(row["hits"])) for row in rows]
