"""Source inventory and processing requests on the application database."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from backend.api.voice_capture import record_json
from backend.knowledge_bases.base import KnowledgeBaseSpec
from backend.src.services.source_pipeline import NO_CONVERTER
from backend.src.services.voice_capture import MAX_TEXT_CHARS
from backend.src.storage.models import KnowledgeStatus
from backend.src.storage.repositories.chunks import ChunkRepository
from backend.src.storage.repositories.ingestion_jobs import IngestionJobRepository, JobConflict
from backend.src.storage.repositories.sources import SourceEntry, SourceRepository


PAGE_SIZE = 100
PREVIEW_CHARS = 100_000
DECISIONS = {"approve": "approved", "reject": "deprecated", "reset": "draft"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log",
}


def review_state(counts: dict[str, int], extracted: bool) -> str:
    """Derive the source-level review label from its chunk counts."""

    if not extracted or not sum(counts.values()):
        return "none"
    if counts.get("draft", 0):
        return "pending"
    if counts.get("deprecated", 0) and not counts.get("approved", 0):
        return "rejected"
    return "reviewed"


class ApplicationKnowledgeManagementService:
    """Read sources and queue their processing through application repositories."""

    def __init__(
        self,
        catalog: Any,
        sources: SourceRepository,
        chunks: ChunkRepository,
        jobs: IngestionJobRepository,
    ) -> None:
        self.catalog = catalog
        self.sources = sources
        self.chunks = chunks
        self.jobs = jobs

    def spec(self, kb: str) -> tuple[UUID, KnowledgeBaseSpec]:
        """Resolve an active knowledge-base name or return HTTP 404."""

        resolved = self.catalog.resolve(kb)
        if resolved is None:
            raise HTTPException(404, "Knowledge base not found")
        return resolved

    @staticmethod
    def _convertible(source: SourceEntry) -> bool:
        if source.file is None:
            return False
        return (
            source.file.content_type.startswith("text/")
            or Path(source.file.filename).suffix.lower() in TEXT_EXTENSIONS
        )

    @staticmethod
    def _processing(source: SourceEntry, job: Any | None) -> dict[str, Any]:
        state = dict(
            source.metadata.get("management")
            or source.metadata.get("ingestion")
            or {}
        )
        if job is not None:
            state["job"] = job.to_json()
            state["status"] = job.status
            if job.mode:
                state["mode"] = job.mode
            if job.error:
                state["error"] = job.error
        return state

    def _summary(self, kb: str, source: SourceEntry, job: Any | None) -> dict[str, Any]:
        counts = dict(source.chunk_counts)
        extracted = source.status == "processed" or bool(sum(counts.values()))
        converted = source.extracted_text is not None
        processing = self._processing(source, job)
        mode = job.mode if job is not None else processing.get("mode")
        if mode is None and source.metadata.get("extraction_mode") in {
            "knowledge", "documents",
        }:
            mode = source.metadata["extraction_mode"]
        kind = (
            "upload"
            if source.kind == "document" and source.file is not None
            else "conversation"
        )
        convertible = self._convertible(source)
        return {
            "id": str(source.id),
            "kind": kind,
            "kb": kb,
            "name": source.title,
            "created_at": source.created_at.isoformat(),
            "content_type": source.file.content_type if source.file else "text/plain",
            "stage": "extracted" if extracted else "converted" if converted else "raw",
            "mode": mode,
            "review": review_state(counts, extracted),
            "counts": counts,
            "processing": processing,
            "can_convert": source.file is not None and not converted and convertible,
            "can_extract_raw": source.file is not None and not converted and convertible,
            "conversion_note": None if converted or convertible else NO_CONVERTER,
            "source_kind": source.metadata.get("source_kind")
            or ("document_upload" if kind == "upload" else "text"),
        }

    async def inventory(self, kb: str, offset: int = 0) -> dict[str, Any]:
        """Return one source page with chunk counts and each latest job."""

        knowledge_base_id, spec = self.spec(kb)
        rows, total = await self.sources.list_page(
            knowledge_base_id, limit=PAGE_SIZE, offset=offset
        )
        jobs = await self.jobs.latest(knowledge_base_id, [row.id for row in rows])
        return {
            "sources": [self._summary(kb, row, jobs.get(row.id)) for row in rows],
            "warnings": [],
            "next_offset": offset + PAGE_SIZE if offset + len(rows) < total else None,
            "qa_mode": spec.retrieval.value,
        }

    async def detail(self, kb: str, kind: str, source_id: str) -> dict[str, Any]:
        """Return source text and every record or passage linked to it."""

        knowledge_base_id, _spec = self.spec(kb)
        try:
            identifier = UUID(source_id)
        except ValueError as exc:
            raise HTTPException(422, "Invalid source ID") from exc
        source = await self.sources.get(knowledge_base_id, identifier)
        if source is None:
            raise HTTPException(404, "Source not found")
        jobs = await self.jobs.latest(knowledge_base_id, [identifier])
        records = await self.chunks.records_for_source(knowledge_base_id, identifier)
        passages = await self.chunks.passages_for_source(knowledge_base_id, identifier)
        counts = Counter(record.status.value for record in records)
        counts.update(chunk["status"] for chunk in passages)
        source = replace(source, chunk_counts=dict(counts))
        summary = self._summary(kb, source, jobs.get(identifier))
        if passages:
            summary["mode"] = "documents"
        elif records:
            summary["mode"] = "knowledge"
        text = source.extracted_text
        conversation = None
        if source.kind == "text":
            conversation = {
                "id": str(source.id),
                "kb": kb,
                "source": "voice" if source.file else "text",
                "name": source.title,
                "recorded_at": source.metadata.get("recorded_at"),
                "created_at": source.created_at.isoformat(),
                "status": (
                    "processed"
                    if source.status == "processed"
                    else "failed" if source.status == "failed" else "pending"
                ),
                "audio": {
                    **(source.metadata.get("audio") or {}),
                    "available": source.file is not None,
                },
                "asr": source.metadata.get("asr") or {},
                "speakers": [
                    {"label": label, "name": (value or {}).get("name")}
                    for label, value in (
                        source.metadata.get("speakers") or {}
                    ).items()
                ],
                "turns": source.metadata.get("turns") or [],
                "cleaning": source.metadata.get("cleaning") or {},
                "ingestion": source.metadata.get("ingestion") or {},
                "records": [record_json(record) for record in records],
                "merged_into": [],
            }
        return {
            **summary,
            "text": text[:PREVIEW_CHARS] if text is not None else None,
            "text_truncated": text is not None and len(text) > PREVIEW_CHARS,
            "records": [record_json(record) for record in records],
            "chunks": passages,
            "conversation": conversation,
            "evidence_id": str(source.id),
        }

    async def enqueue(self, kb: str, stage: str, source_id: UUID, *, mode: str | None = None, next_stages: tuple[str, ...] = (), options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Queue one source stage and translate an active-job conflict to HTTP 409."""

        knowledge_base_id, _spec = self.spec(kb)
        try:
            job = await self.jobs.enqueue(knowledge_base_id, source_id, stage, mode=mode, next_stages=next_stages, options=options)
        except JobConflict as exc:
            raise HTTPException(409, "This source is already being processed") from exc
        return {"job": job.to_json()}

    async def convert(self, kb: str, source_id: str) -> dict[str, Any]:
        """Queue conversion for a raw, text-convertible file source."""

        knowledge_base_id, _spec = self.spec(kb)
        identifier = UUID(source_id)
        source = await self.sources.get(knowledge_base_id, identifier)
        if source is None:
            raise HTTPException(404, "Source not found")
        if source.extracted_text is not None:
            raise HTTPException(409, "This file is already converted")
        if not self._convertible(source):
            raise HTTPException(422, NO_CONVERTER)
        return await self.enqueue(kb, "convert", identifier)

    async def extract(self, kb: str, kind: str, source_id: str, mode: str, *, confirm_overlap: bool = False, overlap_in_worker: bool = False) -> dict[str, Any]:
        """Queue conversion when required, followed by source extraction."""

        knowledge_base_id, _spec = self.spec(kb)
        identifier = UUID(source_id)
        source = await self.sources.get(knowledge_base_id, identifier)
        if source is None:
            raise HTTPException(404, "Source not found")
        records = await self.chunks.records_for_source(
            knowledge_base_id, identifier
        )
        passages = await self.chunks.passages_for_source(
            knowledge_base_id, identifier
        )
        if records or passages:
            raise HTTPException(409, "This source already has extracted results")
        if mode == "knowledge" and source.extracted_text is not None and len(source.extracted_text) > MAX_TEXT_CHARS:
            raise HTTPException(422, f"Knowledge extraction accepts at most {MAX_TEXT_CHARS} characters")
        options = {"confirm_overlap": True} if confirm_overlap else None
        stage = "extract" if source.extracted_text is not None else "convert"
        if stage == "convert" and not self._convertible(source):
            raise HTTPException(409, NO_CONVERTER)
        next_stages = ("extract",) if stage == "convert" else ()
        queued = await self.enqueue(kb, stage, identifier, mode=mode, next_stages=next_stages, options=options)
        return {**queued, "overlaps": []}

    async def review_record(self, kb: str, record_id: UUID, decision: str, note: str, reviewer_id: UUID) -> dict[str, Any]:
        """Review a knowledge record as the signed-in expert."""

        knowledge_base_id, _spec = self.spec(kb)
        status = KnowledgeStatus(DECISIONS[decision])
        event = {"action": "reviewed", "decision": decision, "status": status.value, "note": note.strip() or None, "actor": str(reviewer_id)}
        record = await self.chunks.review_record(knowledge_base_id, record_id, status, reviewer_id=reviewer_id, event=event)
        if record is None:
            raise HTTPException(404, "Knowledge record not found, or superseded")
        return record_json(record)

    async def review_chunk(
        self,
        kb: str,
        kind: str,
        source_id: str,
        chunk_id: str,
        decision: str,
        note: str,
        reviewer_id: UUID,
    ) -> dict[str, Any]:
        """Review a document passage linked to a source as the signed-in expert."""

        knowledge_base_id, _spec = self.spec(kb)
        try:
            source_uuid = UUID(source_id)
            chunk_uuid = UUID(chunk_id)
        except ValueError as exc:
            raise HTTPException(422, "Invalid source or chunk ID") from exc
        status = KnowledgeStatus(DECISIONS[decision])
        event = {
            "action": "reviewed",
            "decision": decision,
            "status": status.value,
            "note": note.strip() or None,
            "actor": str(reviewer_id),
        }
        changed = await self.chunks.review_passage(
            knowledge_base_id,
            source_uuid,
            chunk_uuid,
            status,
            reviewer_id=reviewer_id,
            event=event,
        )
        if not changed:
            raise HTTPException(404, "Chunk not found for this source")
        return await self.detail(kb, kind, source_id)

    async def queue_conflicts(self, _kb: str, _evidence_id: UUID) -> None:
        """Remain compatible until conflict execution moves in Phase 3."""

    async def conflict_list(
        self,
        kb: str,
        *,
        status: str | None = None,
        evidence_id: UUID | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return an empty conflict page until Phase 3 wires conflict rows."""

        self.spec(kb)
        return {
            "conflicts": [],
            "counts": {},
            "offset": offset,
            "limit": 50,
            "next_offset": None,
            "enabled": False,
        }

    async def review_conflict(self, *_args: Any, **_kwargs: Any) -> None:
        """Reject reviews while no application conflict rows are exposed."""

        raise HTTPException(404, "Conflict not found")

    async def recheck_conflicts(self, kb: str, _record_ids: Any = ()) -> None:
        """Reject rechecks until Phase 3 wires conflict processing."""

        self.spec(kb)
        raise HTTPException(404, "Conflict detection is not available")

    async def aclose(self) -> None:
        """Repositories own no persistent connection resources."""

