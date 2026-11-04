"""Knowledge collections, their original sources and searchable extracted knowledge."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.storage.orm.base import (
    TEXT_SEARCH_CONFIG,
    Base,
    RecordDates,
    allowed_values,
    json_object,
    uuid_primary_key,
)


class SourceKind(StrEnum):
    """Form of an original item submitted to create knowledge."""

    DOCUMENT = "document"
    IMAGE = "image"
    TEXT = "text"


class SourceStatus(StrEnum):
    """Processing state of a knowledge source; attempts are recorded separately."""

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class ChunkStatus(StrEnum):
    """Review state of extracted knowledge.

    Drafts stay searchable with their status shown; deprecated chunks remain
    for history and citations but are excluded from search.
    """

    DRAFT = "draft"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class KnowledgeBase(RecordDates, Base):
    """A knowledge collection; its content lives in sources and chunks."""

    __tablename__ = "knowledge_bases"

    id: Mapped[UUID] = uuid_primary_key()
    # Stable name, unique even after soft deletion.
    name: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # Built-in knowledge base whose extraction and retrieval policy this one copies.
    template: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class KnowledgeSource(RecordDates, Base):
    """One original document, handwriting image or pasted text.

    Employee-agent chats are not knowledge sources. A source may finish
    processing without producing chunks when it holds no usable knowledge.
    """

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        allowed_values("kind", SourceKind),
        allowed_values("status", SourceStatus),
        CheckConstraint(
            "kind = 'text' OR file_id IS NOT NULL", name="file_required_for_kind"
        ),
        json_object("metadata"),
        Index(
            "ix_knowledge_sources_active_original",
            "knowledge_base_id",
            "original_sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND original_sha256 IS NOT NULL"),
        ),
        # Target of composite references that keep processing records in the
        # source's collection.
        UniqueConstraint("id", "knowledge_base_id"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id"), index=True
    )
    file_id: Mapped[UUID | None] = mapped_column(ForeignKey("files.id"), index=True)
    original_sha256: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    # NULL until the source is converted to text.
    extracted_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text,
        default=SourceStatus.RECEIVED.value,
        server_default=SourceStatus.RECEIVED.value,
    )
    # `metadata` is reserved by SQLAlchemy's declarative classes.
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    submitted_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))

    chunk_links: Mapped[list[KnowledgeChunkSource]] = relationship(
        back_populates="source", lazy="raise"
    )


class KnowledgeChunk(RecordDates, Base):
    """One extracted knowledge record that can be searched and shown as a case card.

    Corrections create a replacement chunk linked through `superseded_by`.
    The embedding HNSW index depends on the embedding size, so the hand-written
    database-rules migration defines it rather than this model.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        allowed_values("status", ChunkStatus),
        json_object("details"),
        CheckConstraint(
            "(reviewed_by IS NULL) = (reviewed_at IS NULL)",
            name="review_fields_together",
        ),
        CheckConstraint(
            "superseded_by IS NULL OR superseded_by <> id",
            name="not_superseded_by_itself",
        ),
        Index(None, "search_vector", postgresql_using="gin"),
        # Target of composite references that keep conflict pairs in one collection.
        UniqueConstraint("id", "knowledge_base_id"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    search_text: Mapped[str] = mapped_column(Text)
    # Size comes from EMBEDDING_DIM when the migration runs. A trigger clears
    # the embedding when search_text changes without a new embedding.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(), deferred=True, deferred_raiseload=True
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            f"to_tsvector('{TEXT_SEARCH_CONFIG}'::regconfig, search_text)",
            persisted=True,
        ),
        deferred=True,
        deferred_raiseload=True,
    )
    status: Mapped[str] = mapped_column(
        Text, default=ChunkStatus.DRAFT.value, server_default=ChunkStatus.DRAFT.value
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_chunks.id"), index=True
    )

    source_links: Mapped[list[KnowledgeChunkSource]] = relationship(
        back_populates="chunk", lazy="raise"
    )


class KnowledgeChunkSource(Base):
    """Evidence link from a chunk to an original source in the same collection.

    `references` lists the supporting pages, exact quotations or marked image
    areas. Links preserve evidence and are not hidden or deleted on their own.
    """

    __tablename__ = "knowledge_chunk_sources"
    __table_args__ = (
        CheckConstraint(
            # CASE avoids calling jsonb_array_length on a non-array value.
            "CASE WHEN jsonb_typeof(\"references\") = 'array' "
            "THEN jsonb_array_length(\"references\") > 0 ELSE false END",
            name="references_not_empty",
        ),
    )

    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_chunks.id"), primary_key=True
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_sources.id"), primary_key=True, index=True
    )
    references: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    chunk: Mapped[KnowledgeChunk] = relationship(
        back_populates="source_links", lazy="raise"
    )
    source: Mapped[KnowledgeSource] = relationship(
        back_populates="chunk_links", lazy="raise"
    )
