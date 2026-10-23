"""Extraction runs: which content was extracted, in which mode, with which logic.

Each extraction of an evidence row (`raw_conversations`) is one run row in the
KB's own database. Two fingerprints identify it:

- the content fingerprint: SHA-256 of the evidence text after Unicode NFC and
  whitespace normalization, so a re-upload of the same text matches even when
  line endings or spacing differ (`original_sha256` keeps the exact bytes);
- the extraction fingerprint: SHA-256 of the mode and its settings (prompt,
  model and schema/ontology hashes for knowledge extraction; embedding and
  chunking settings for document chunks).

A running or done run blocks extracting the same content in the same mode
from another evidence row. A unique partial index guarantees this for one
extraction logic; a transaction lock on content+mode serializes the check
across logics. Re-extraction with changed logic (a staged run, review and
switch) is not implemented yet. Runs are history: they are finished, never
deleted, and extraction results are never overwritten here.

`content_signatures` keeps the MinHash LSH bands of each extracted evidence
row (`content_signatures.py`), so near-duplicates of a new text are found
among the rows with a running or done run in the same mode.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.src.storage.knowledge_store import KNOWLEDGE_TABLE, RAW_CONVERSATION_TABLE, StorageError


RUNS_TABLE = "extraction_runs"
SIGNATURES_TABLE = "content_signatures"
ACTIVE = ("running", "done")
LEGACY_FINGERPRINT = "legacy"
STATUSES = ("running", "done", "failed", "duplicate")


def normalized_content(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def content_sha256(value: str) -> str:
    return hashlib.sha256(normalized_content(value).encode("utf-8")).hexdigest()


def extraction_fingerprint(mode: str, settings: dict[str, Any]) -> str:
    payload = json.dumps({"mode": mode, **settings}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stale_after_from_env() -> float:
    raw = os.getenv("EXTRACTION_RUN_STALE_S", "").strip()
    return max(60.0, float(raw)) if raw else 3600.0


@dataclass(frozen=True)
class ExtractionRun:
    id: UUID
    evidence_id: UUID
    content_sha256: str
    mode: str
    fingerprint: str
    status: str
    created_at: datetime
    updated_at: datetime
    original_sha256: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    finished_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def to_json(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.id), "evidence_id": str(self.evidence_id), "mode": self.mode,
            "status": self.status, "content_sha256": self.content_sha256,
            "fingerprint": self.fingerprint, "created_at": iso(self.created_at),
            "finished_at": iso(self.finished_at),
        }


class RunConflict(StorageError):
    """The content already has a running or done run (on another evidence row)."""

    def __init__(self, run: ExtractionRun) -> None:
        super().__init__(f"Content already extracted by run {run.id}")
        self.run = run


@dataclass(frozen=True)
class Unrecorded:
    """An extracted evidence row with no run (extracted before runs existed)."""

    id: UUID
    transcript: str
    metadata: dict[str, Any]
    has_knowledge: bool


def _checked(mode: str, status: str = "running") -> None:
    if mode not in ("knowledge", "documents"):
        raise ValueError(f"Unknown extraction mode: {mode!r}")
    if status not in STATUSES:
        raise ValueError(f"Unknown run status: {status!r}")


# ------------------------------------------------------------------ PostgreSQL


class ExtractionRunStore:
    """Run rows next to `raw_conversations` in a KB database."""

    def __init__(self, engine: AsyncEngine, *, schema: str = "public",
                 stale_after_s: float | None = None) -> None:
        self._engine = engine
        self._schema = schema
        self._table = f'"{schema}".{RUNS_TABLE}'
        self._raw = f'"{schema}".{RAW_CONVERSATION_TABLE}'
        self._signatures = f'"{schema}".{SIGNATURES_TABLE}'
        self.stale_after_s = stale_after_from_env() if stale_after_s is None else stale_after_s

    async def init_schema(self) -> None:
        name = f"{self._schema}_{RUNS_TABLE}"[:50]
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": self._table})
                await conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        id uuid PRIMARY KEY,
                        evidence_id uuid NOT NULL REFERENCES {self._raw} (id),
                        content_sha256 text NOT NULL,
                        original_sha256 text,
                        mode text NOT NULL CHECK (mode IN ('knowledge', 'documents')),
                        fingerprint text NOT NULL,
                        settings jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                        status text NOT NULL
                            CHECK (status IN ('running', 'done', 'failed', 'duplicate')),
                        job_id text,
                        result jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                        error text,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        finished_at timestamptz
                    )
                """))
                # Idempotency: one running or done run per content, mode and logic.
                await conn.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {name}_active ON {self._table} "
                    "(content_sha256, mode, fingerprint) WHERE status IN ('running', 'done')"
                ))
                await conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {name}_evidence ON {self._table} (evidence_id, created_at DESC)"
                ))
                await conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {name}_original ON {self._table} (original_sha256, mode) "
                    "WHERE status IN ('running', 'done')"
                ))
                await conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {name}_fingerprint ON {self._table} (fingerprint, status)"
                ))
                await conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS {self._signatures} (
                        evidence_id uuid PRIMARY KEY REFERENCES {self._raw} (id),
                        content_sha256 text NOT NULL,
                        version text NOT NULL,
                        shingle_count integer NOT NULL,
                        bands text[] NOT NULL,
                        created_at timestamptz NOT NULL DEFAULT now()
                    )
                """))
                signatures = f"{self._schema}_{SIGNATURES_TABLE}"[:50]
                await conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {signatures}_bands ON {self._signatures} USING gin (bands)"
                ))
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not prepare extraction runs: {exc}") from exc

    @staticmethod
    def _run(row: Any) -> ExtractionRun:
        values = dict(row)
        values["settings"] = values.get("settings") or {}
        values["result"] = values.get("result") or {}
        return ExtractionRun(**values)

    async def _first(self, where: str, parameters: dict[str, Any]) -> ExtractionRun | None:
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(text(
                    f"SELECT * FROM {self._table} WHERE {where} "
                    "ORDER BY (status = 'done') DESC, created_at LIMIT 1"
                ), parameters)).mappings().first()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read extraction runs: {exc}") from exc
        return self._run(row) if row else None

    # Stale running runs do not block: the next start marks them failed.
    _LIVE = ("(status = 'done' OR (status = 'running' "
             "AND updated_at >= now() - make_interval(secs => :stale)))")

    async def find_active(self, content: str, mode: str, *, exclude: UUID | None = None) -> ExtractionRun | None:
        """A done or live running run of this content and mode, done runs first."""

        return await self._first(
            f"content_sha256 = :content AND mode = :mode AND {self._LIVE} "
            "AND (CAST(:exclude AS uuid) IS NULL OR evidence_id <> CAST(:exclude AS uuid))",
            {"content": content, "mode": mode, "exclude": exclude, "stale": self.stale_after_s},
        )

    async def find_active_original(self, original: str, mode: str) -> ExtractionRun | None:
        """A done or live running run of an upload with the same original bytes."""

        return await self._first(
            f"original_sha256 = :original AND mode = :mode AND {self._LIVE}",
            {"original": original, "mode": mode, "stale": self.stale_after_s},
        )

    async def for_evidence(self, evidence_id: UUID) -> list[ExtractionRun]:
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(text(
                    f"SELECT * FROM {self._table} WHERE evidence_id = :id ORDER BY created_at DESC"
                ), {"id": evidence_id})).mappings().all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read extraction runs: {exc}") from exc
        return [self._run(row) for row in rows]

    async def start(self, *, evidence_id: UUID, content: str, mode: str, fingerprint: str,
                    settings: dict[str, Any], original_sha256: str | None = None,
                    job_id: str | None = None) -> ExtractionRun:
        """Starts a run, or raises RunConflict with the run that already covers the content.

        A running run of the same evidence row (a retried or requeued job) is
        taken over. A running run that has not been updated for
        `stale_after_s` (its worker died for good) is marked failed first.
        """

        _checked(mode)
        try:
            async with self._engine.begin() as conn:
                # Serializes starts of one content+mode across extraction logics.
                await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                                   {"key": f"{self._table}:{content}:{mode}"})
                rows = (await conn.execute(text(
                    f"SELECT * FROM {self._table} WHERE content_sha256 = :content AND mode = :mode "
                    "AND status IN ('running', 'done') ORDER BY (status = 'done') DESC, created_at "
                    "FOR UPDATE"
                ), {"content": content, "mode": mode})).mappings().all()
                stale = datetime.now(timezone.utc) - timedelta(seconds=self.stale_after_s)
                for row in rows:
                    run = self._run(row)
                    if run.status == "running" and run.evidence_id == evidence_id:
                        updated = (await conn.execute(text(
                            f"UPDATE {self._table} SET job_id = :job, fingerprint = :fingerprint, "
                            "settings = CAST(:settings AS jsonb), updated_at = now() "
                            "WHERE id = :id RETURNING *"
                        ), {"id": run.id, "job": job_id, "fingerprint": fingerprint,
                            "settings": json.dumps(settings, ensure_ascii=False, default=str)}
                        )).mappings().one()
                        return self._run(updated)
                    if run.status == "running" and run.updated_at < stale:
                        await conn.execute(text(
                            f"UPDATE {self._table} SET status = 'failed', error = :error, "
                            "finished_at = now(), updated_at = now() WHERE id = :id"
                        ), {"id": run.id, "error": ABANDONED})
                        continue
                    raise RunConflict(run)
                row = (await conn.execute(text(
                    f"INSERT INTO {self._table} (id, evidence_id, content_sha256, original_sha256, mode, "
                    "fingerprint, settings, status, job_id) VALUES (:id, :evidence, :content, :original, "
                    ":mode, :fingerprint, CAST(:settings AS jsonb), 'running', :job) RETURNING *"
                ), {"id": uuid4(), "evidence": evidence_id, "content": content, "original": original_sha256,
                    "mode": mode, "fingerprint": fingerprint, "job": job_id,
                    "settings": json.dumps(settings, ensure_ascii=False, default=str)})).mappings().one()
                return self._run(row)
        except IntegrityError as exc:
            # Only reachable without the lock (another writer); report what won.
            run = await self.find_active(content, mode)
            if run is None:
                raise StorageError(f"Could not start the extraction run: {exc}") from exc
            raise RunConflict(run) from exc
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not start the extraction run: {exc}") from exc

    async def finish(self, run_id: UUID, status: str, *, result: dict[str, Any] | None = None,
                     error: str | None = None) -> None:
        _checked("knowledge", status)
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text(
                    f"UPDATE {self._table} SET status = :status, result = CAST(:result AS jsonb), "
                    "error = :error, finished_at = now(), updated_at = now() "
                    "WHERE id = :id AND status = 'running'"
                ), {"id": run_id, "status": status, "error": error and error[:2000],
                    "result": json.dumps(result or {}, ensure_ascii=False, default=str)})
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not finish extraction run {run_id}: {exc}") from exc

    async def unrecorded(self, limit: int = 200) -> list[Unrecorded]:
        """Extracted evidence rows without a run (oldest first)."""

        knowledge = f'"{self._schema}".{KNOWLEDGE_TABLE}'
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(text(
                    f"""
                    SELECT c.id, c.transcript, c.metadata,
                           EXISTS (SELECT 1 FROM {knowledge} AS k WHERE k.evidence_id = c.id) AS has_knowledge
                    FROM {self._raw} AS c
                    WHERE (c.status = 'processed'
                           OR EXISTS (SELECT 1 FROM {knowledge} AS k WHERE k.evidence_id = c.id))
                      AND NOT EXISTS (SELECT 1 FROM {self._table} AS r WHERE r.evidence_id = c.id
                                      AND r.status IN ('running', 'done', 'duplicate'))
                    ORDER BY c.created_at, c.id
                    LIMIT :limit
                    """
                ), {"limit": limit})).mappings().all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read unrecorded extractions: {exc}") from exc
        return [Unrecorded(row["id"], row["transcript"], row["metadata"] or {}, row["has_knowledge"])
                for row in rows]

    async def record_legacy(self, *, evidence_id: UUID, content: str, mode: str, fingerprint: str,
                            settings: dict[str, Any], original_sha256: str | None = None,
                            created_at: datetime | None = None) -> ExtractionRun:
        """Records an earlier extraction as done, or as `duplicate` of an existing run."""

        _checked(mode)
        values = {"id": uuid4(), "evidence": evidence_id, "content": content, "original": original_sha256,
                  "mode": mode, "fingerprint": fingerprint,
                  "settings": json.dumps(settings, ensure_ascii=False, default=str),
                  "result": json.dumps({"backfilled": True}), "created": created_at}
        insert = (
            f"INSERT INTO {self._table} (id, evidence_id, content_sha256, original_sha256, mode, "
            "fingerprint, settings, status, result, created_at, finished_at) VALUES (:id, :evidence, "
            ":content, :original, :mode, :fingerprint, CAST(:settings AS jsonb), :status, "
            "CAST(:result AS jsonb), COALESCE(:created, now()), COALESCE(:created, now())) "
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                                   {"key": f"{self._table}:{content}:{mode}"})
                row = (await conn.execute(text(
                    insert + "ON CONFLICT (content_sha256, mode, fingerprint) "
                    "WHERE status IN ('running', 'done') DO NOTHING RETURNING *"
                ), {**values, "status": "done"})).mappings().first()
                if row is None:
                    row = (await conn.execute(text(insert + "RETURNING *"),
                                              {**values, "status": "duplicate"})).mappings().one()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not record extraction of {evidence_id}: {exc}") from exc
        return self._run(row)


    # ------------------------------------------------------------ signatures

    async def put_signature(self, evidence_id: UUID, *, content: str, version: str,
                            shingle_count: int, bands: list[str]) -> None:
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text(
                    f"INSERT INTO {self._signatures} (evidence_id, content_sha256, version, shingle_count, bands) "
                    "VALUES (:id, :content, :version, :count, CAST(:bands AS text[])) "
                    "ON CONFLICT (evidence_id) DO UPDATE SET content_sha256 = EXCLUDED.content_sha256, "
                    "version = EXCLUDED.version, shingle_count = EXCLUDED.shingle_count, "
                    "bands = EXCLUDED.bands, created_at = now()"
                ), {"id": evidence_id, "content": content, "version": version, "count": shingle_count,
                    "bands": bands})
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not store the signature of {evidence_id}: {exc}") from exc

    async def unsigned(self, version: str, limit: int = 100) -> list[UUID]:
        """Evidence rows with a running or done run but no current signature."""

        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(text(
                    f"SELECT DISTINCT r.evidence_id FROM {self._table} AS r "
                    f"LEFT JOIN {self._signatures} AS s ON s.evidence_id = r.evidence_id "
                    "AND s.version = :version "
                    "WHERE r.status IN ('running', 'done') AND s.evidence_id IS NULL LIMIT :limit"
                ), {"version": version, "limit": limit})).all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read unsigned extractions: {exc}") from exc
        return [row[0] for row in rows]

    async def overlap_candidates(self, bands: list[str], mode: str, *, version: str, content: str,
                                 exclude: UUID | None = None, limit: int = 20) -> list[tuple[ExtractionRun, int]]:
        """Extracted rows sharing LSH bands with `bands`, most shared bands first.

        Rows with the same content (exact duplicates) and `exclude` are left out;
        each row comes with its done (or live running) run in `mode`.
        """

        if not bands:
            return []
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(text(
                    f"""
                    SELECT r.*, h.hits FROM (
                        SELECT s.evidence_id,
                               (SELECT count(*) FROM unnest(s.bands) AS b
                                WHERE b = ANY(CAST(:bands AS text[]))) AS hits
                        FROM {self._signatures} AS s
                        WHERE s.bands && CAST(:bands AS text[]) AND s.version = :version
                          AND s.content_sha256 <> :content
                          AND (CAST(:exclude AS uuid) IS NULL OR s.evidence_id <> CAST(:exclude AS uuid))
                    ) AS h
                    JOIN LATERAL (
                        SELECT * FROM {self._table}
                        WHERE evidence_id = h.evidence_id AND mode = :mode AND {self._LIVE}
                        ORDER BY (status = 'done') DESC, created_at LIMIT 1
                    ) AS r ON true
                    ORDER BY h.hits DESC, r.created_at
                    LIMIT :limit
                    """
                ), {"bands": bands, "version": version, "content": content, "exclude": exclude,
                    "mode": mode, "stale": self.stale_after_s, "limit": limit})).mappings().all()
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not look up similar extractions: {exc}") from exc
        found = []
        for row in rows:
            values = dict(row)
            hits = values.pop("hits")
            found.append((self._run(values), int(hits)))
        return found


ABANDONED = "Lần trích xuất dừng giữa chừng quá lâu; đã đánh dấu thất bại để nguồn khác có thể trích xuất."


# ------------------------------------------------------------------ in memory


class MemoryExtractionRunStore:
    """The same contract in one process. `extracted` lists unrecorded rows for backfill."""

    def __init__(self, extracted: Callable[[], Iterable[Unrecorded]] | None = None, *,
                 stale_after_s: float = 3600.0) -> None:
        self.runs: dict[UUID, ExtractionRun] = {}
        self.signatures: dict[UUID, tuple[str, str, int, frozenset[str]]] = {}
        self._extracted = extracted
        self.stale_after_s = stale_after_s

    async def init_schema(self) -> None:
        pass

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _active(self, predicate, *, live=False) -> list[ExtractionRun]:
        stale = self._now() - timedelta(seconds=self.stale_after_s)
        return sorted((run for run in self.runs.values() if run.active and predicate(run)
                       and not (live and run.status == "running" and run.updated_at < stale)),
                      key=lambda run: (run.status != "done", run.created_at))

    async def find_active(self, content, mode, *, exclude=None):
        found = self._active(lambda run: run.content_sha256 == content and run.mode == mode
                             and run.evidence_id != exclude, live=True)
        return found[0] if found else None

    async def find_active_original(self, original, mode):
        found = self._active(lambda run: run.original_sha256 == original and run.mode == mode, live=True)
        return found[0] if found else None

    async def for_evidence(self, evidence_id):
        return sorted((run for run in self.runs.values() if run.evidence_id == evidence_id),
                      key=lambda run: run.created_at, reverse=True)

    async def start(self, *, evidence_id, content, mode, fingerprint, settings,
                    original_sha256=None, job_id=None):
        _checked(mode)
        stale = self._now() - timedelta(seconds=self.stale_after_s)
        for run in self._active(lambda run: run.content_sha256 == content and run.mode == mode):
            if run.status == "running" and run.evidence_id == evidence_id:
                run = replace(run, job_id=job_id, fingerprint=fingerprint, settings=dict(settings),
                              updated_at=self._now())
                self.runs[run.id] = run
                return run
            if run.status == "running" and run.updated_at < stale:
                self.runs[run.id] = replace(run, status="failed", error=ABANDONED,
                                            finished_at=self._now(), updated_at=self._now())
                continue
            raise RunConflict(run)
        now = self._now()
        run = ExtractionRun(id=uuid4(), evidence_id=evidence_id, content_sha256=content, mode=mode,
                            fingerprint=fingerprint, status="running", created_at=now, updated_at=now,
                            original_sha256=original_sha256, settings=dict(settings), job_id=job_id)
        self.runs[run.id] = run
        return run

    async def finish(self, run_id, status, *, result=None, error=None):
        _checked("knowledge", status)
        run = self.runs.get(run_id)
        if run is not None and run.status == "running":
            self.runs[run_id] = replace(run, status=status, result=dict(result or {}), error=error,
                                        finished_at=self._now(), updated_at=self._now())

    async def unrecorded(self, limit=200):
        recorded = {run.evidence_id for run in self.runs.values()
                    if run.status in ("running", "done", "duplicate")}
        rows = [row for row in (self._extracted() if self._extracted else []) if row.id not in recorded]
        return rows[:limit]

    async def record_legacy(self, *, evidence_id, content, mode, fingerprint, settings,
                            original_sha256=None, created_at=None):
        _checked(mode)
        taken = self._active(lambda run: (run.content_sha256, run.mode, run.fingerprint)
                             == (content, mode, fingerprint))
        when = created_at or self._now()
        run = ExtractionRun(id=uuid4(), evidence_id=evidence_id, content_sha256=content, mode=mode,
                            fingerprint=fingerprint, status="duplicate" if taken else "done",
                            created_at=when, updated_at=when, finished_at=when,
                            original_sha256=original_sha256, settings=dict(settings),
                            result={"backfilled": True})
        self.runs[run.id] = run
        return run

    async def put_signature(self, evidence_id, *, content, version, shingle_count, bands):
        self.signatures[evidence_id] = (content, version, shingle_count, frozenset(bands))

    async def unsigned(self, version, limit=100):
        found = []
        for run in self.runs.values():
            signed = self.signatures.get(run.evidence_id)
            if run.active and run.evidence_id not in found and (signed is None or signed[1] != version):
                found.append(run.evidence_id)
        return found[:limit]

    async def overlap_candidates(self, bands, mode, *, version, content, exclude=None, limit=20):
        wanted, found = set(bands), []
        for evidence_id, (signed, signed_version, _, keys) in self.signatures.items():
            hits = len(wanted & keys)
            if not hits or signed_version != version or signed == content or evidence_id == exclude:
                continue
            runs = self._active(lambda run: run.evidence_id == evidence_id and run.mode == mode, live=True)
            if runs:
                found.append((runs[0], hits))
        found.sort(key=lambda item: (-item[1], item[0].created_at))
        return found[:limit]
