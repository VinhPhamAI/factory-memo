"""Processing records of knowledge sources: stage jobs, extraction runs,
near-duplicate signatures and suspected knowledge conflicts.

These replace the tables that service code creates today in the DATABASE_URL
database (`ingestion_jobs`) and in each `kb_<name>` database (the other three).
Text knowledge-base names and evidence IDs become references to shared
`knowledge_bases`, `knowledge_sources` and `knowledge_chunks` rows, so records
that were unique within one collection database now include
`knowledge_base_id` in their unique keys.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.orm.base import (
    Base,
    ChangeDates,
    allowed_values,
    json_array,
    json_object,
    sha256_hex,
    uuid_primary_key,
)


class JobStage(StrEnum):
    """Processing stage of a source; a job may name the stages that follow it."""

    CONVERT = "convert"
    EXTRACT = "extract"
    CONFLICT = "conflict"


class JobStatus(StrEnum):
    """Queue state of a stage job; queued and running jobs are active."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ExtractionMode(StrEnum):
    """Extraction output: knowledge records or document chunks."""

    KNOWLEDGE = "knowledge"
    DOCUMENTS = "documents"


class RunStatus(StrEnum):
    """Result of one extraction attempt; running and done runs block repeats."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class ConflictStatus(StrEnum):
    """Judge verdict or reviewer decision for a pair of knowledge chunks.

    `no_conflict`, `open`, `pending` and `needs_review` come from the judge;
    `confirmed`, `dismissed` and `resolved` are reviewer decisions. None of
    them changes the status of the chunks.
    """

    NO_CONFLICT = "no_conflict"
    OPEN = "open"
    PENDING = "pending"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


ACTIVE_JOB = text("status IN ('queued', 'running')")
ACTIVE_RUN = text("status IN ('running', 'done')")


class IngestionJob(ChangeDates, Base):
    """One processing stage of one source, claimed and retried by workers.

    At most one job per source is queued or running. Finishing a job and
    creating its next stage happen in one transaction (backend rule).
    """

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        allowed_values("stage", JobStage),
        allowed_values("mode", ExtractionMode),
        allowed_values("status", JobStatus),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        json_array("next_stages"),
        json_object("result"),
        json_object("options"),
        ForeignKeyConstraint(
            ["source_id", "knowledge_base_id"],
            ["knowledge_sources.id", "knowledge_sources.knowledge_base_id"],
            name="fk_ingestion_jobs_source",
        ),
        Index(
            "ix_ingestion_jobs_active_source",
            "source_id",
            unique=True,
            postgresql_where=ACTIVE_JOB,
        ),
        Index(
            "ix_ingestion_jobs_claim",
            "stage",
            "run_after",
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(None, "source_id", "created_at"),
        Index(None, "knowledge_base_id", "created_at"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    knowledge_base_id: Mapped[UUID] = mapped_column()
    source_id: Mapped[UUID] = mapped_column()
    stage: Mapped[str] = mapped_column(Text)
    # NULL for stages that do not extract.
    mode: Mapped[str | None] = mapped_column(Text)
    next_stages: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Text, default=JobStatus.QUEUED.value, server_default=JobStatus.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # Reviewer requests carried to every stage, such as `confirm_overlap`.
    options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ingestion_jobs.id"), index=True
    )
    locked_by: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractionRun(ChangeDates, Base):
    """One extraction attempt of a source, kept as history.

    A running or done run blocks extracting the same content in the same mode
    and logic again within the collection. Runs are finished, never deleted,
    and their results are never overwritten.
    """

    __tablename__ = "extraction_runs"
    __table_args__ = (
        sha256_hex("content_sha256"),
        sha256_hex("original_sha256"),
        allowed_values("mode", ExtractionMode),
        allowed_values("status", RunStatus),
        json_object("settings"),
        json_object("result"),
        ForeignKeyConstraint(
            ["source_id", "knowledge_base_id"],
            ["knowledge_sources.id", "knowledge_sources.knowledge_base_id"],
            name="fk_extraction_runs_source",
        ),
        Index(
            "ix_extraction_runs_active_content",
            "knowledge_base_id",
            "content_sha256",
            "mode",
            "fingerprint",
            unique=True,
            postgresql_where=ACTIVE_RUN,
        ),
        Index(
            "ix_extraction_runs_active_original",
            "knowledge_base_id",
            "original_sha256",
            "mode",
            postgresql_where=ACTIVE_RUN,
        ),
        Index(None, "source_id", "created_at"),
        Index(None, "fingerprint", "status"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    knowledge_base_id: Mapped[UUID] = mapped_column()
    source_id: Mapped[UUID] = mapped_column()
    # SHA-256 of the text after Unicode NFC and whitespace normalization.
    content_sha256: Mapped[str] = mapped_column(Text)
    # SHA-256 of the exact text, when known.
    original_sha256: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    # SHA-256 of the mode and its settings, or `legacy` for older runs.
    fingerprint: Mapped[str] = mapped_column(Text)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text)
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ingestion_jobs.id"), index=True
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContentSignature(ChangeDates, Base):
    """MinHash LSH bands of an extracted source text, for near-duplicate warnings.

    Sources sharing a band within a collection are candidates; their overlap
    is then scored exactly. Scores only warn reviewers; nothing is merged or
    blocked on similarity.
    """

    __tablename__ = "content_signatures"
    __table_args__ = (
        sha256_hex("content_sha256"),
        CheckConstraint("shingle_count >= 0", name="shingle_count_not_negative"),
        ForeignKeyConstraint(
            ["source_id", "knowledge_base_id"],
            ["knowledge_sources.id", "knowledge_sources.knowledge_base_id"],
            name="fk_content_signatures_source",
        ),
        Index(None, "bands", postgresql_using="gin"),
    )

    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(index=True)
    content_sha256: Mapped[str] = mapped_column(Text)
    # Signature algorithm and parameters; only equal versions are comparable.
    version: Mapped[str] = mapped_column(Text)
    shingle_count: Mapped[int] = mapped_column(Integer)
    bands: Mapped[list[str]] = mapped_column(ARRAY(Text))


class KnowledgeConflict(ChangeDates, Base):
    """A judged pair of knowledge chunks: conflict cache and review queue.

    One row per ordered pair (`a_id < b_id`) and judge fingerprint. Both
    chunks belong to the row's collection. Reviewer decisions are appended to
    `review`; a new judgement after content changes keeps that history.
    """

    __tablename__ = "knowledge_conflicts"
    __table_args__ = (
        allowed_values("status", ConflictStatus),
        CheckConstraint("a_id < b_id", name="pair_ordered"),
        json_array("review"),
        ForeignKeyConstraint(
            ["a_id", "knowledge_base_id"],
            ["knowledge_chunks.id", "knowledge_chunks.knowledge_base_id"],
            name="fk_knowledge_conflicts_a",
        ),
        ForeignKeyConstraint(
            ["b_id", "knowledge_base_id"],
            ["knowledge_chunks.id", "knowledge_chunks.knowledge_base_id"],
            name="fk_knowledge_conflicts_b",
        ),
        UniqueConstraint("a_id", "b_id", "judge_fingerprint"),
        Index(None, "b_id"),
        Index(None, "knowledge_base_id", "status", "created_at"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    knowledge_base_id: Mapped[UUID] = mapped_column()
    a_id: Mapped[UUID] = mapped_column()
    b_id: Mapped[UUID] = mapped_column()
    # Content hashes of both chunks when they were judged.
    a_hash: Mapped[str] = mapped_column(Text)
    b_hash: Mapped[str] = mapped_column(Text)
    # Prompt, model and judge mode that produced the verdict.
    judge_fingerprint: Mapped[str] = mapped_column(Text)
    # The chunk whose extraction led to the judgement.
    new_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_chunks.id"), index=True
    )
    relation: Mapped[str | None] = mapped_column(Text)
    aspect: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Double)
    reason: Mapped[str | None] = mapped_column(Text)
    quotes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    list_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    pair_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    # Whether conflict links were added to the chunks.
    linked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ingestion_jobs.id"), index=True
    )
    review: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
