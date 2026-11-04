"""Source inventory and processing requests on the application database."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from backend.api.voice_capture import record_json
from backend.knowledge_bases.base import KnowledgeBaseSpec
from backend.src.services.converters import can_convert_file
from backend.src.services.source_pipeline import NO_CONVERTER
from backend.src.services.source_pipeline import duplicate_message, overlap_message
from backend.src.services.knowledge_conflicts import link_updates, stale
from backend.src.services.voice_capture import MAX_TEXT_CHARS
from backend.src.storage.content_signatures import (
    CONFIRM_LEVELS,
    SIGNATURE_VERSION,
    Thresholds,
    overlap,
    shingles,
    signature,
)
from backend.src.storage.extraction_runs import content_sha256
from backend.src.storage.knowledge_conflicts import STATUSES as CONFLICT_STATUSES
from backend.src.storage.models import KnowledgeLinkUpdate, KnowledgeStatus
from backend.src.storage.repositories.chunks import ChunkRepository
from backend.src.storage.repositories.extraction_runs import ExtractionRunRepository
from backend.src.storage.repositories.ingestion_jobs import IngestionJobRepository, JobConflict
from backend.src.storage.repositories.knowledge_conflicts import ConflictRepository
from backend.src.storage.repositories.sources import (
    SourceEntry, SourceJobRunning, SourceRemovalForbidden, SourceRepository,
)


PAGE_SIZE = 100
PREVIEW_CHARS = 100_000
DECISIONS = {"approve": "approved", "reject": "deprecated", "reset": "draft"}
CONFLICT_DECISIONS = {"confirm": "confirmed", "dismiss": "dismissed", "supersede": "resolved"}
CONFLICT_PAGE_SIZE = 50
CLAIMING_STATUSES = ("open", "pending", "needs_review", "confirmed")
RECHECK_SCAN_LIMIT = 1000
OVERLAP_CANDIDATES = 20
MAX_OVERLAPS = 5


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
        sessions: Any | None = None,
        *,
        thresholds: Thresholds | None = None,
    ) -> None:
        self.catalog = catalog
        self.sources = sources
        self.chunks = chunks
        self.jobs = jobs
        self.sessions = sessions
        self.thresholds = thresholds or Thresholds.from_env()

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
        return can_convert_file(source.file.content_type, source.file.filename)

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
            if source.kind in {"document", "image"} and source.file is not None
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
            "submitted_by": str(source.submitted_by),
            "removed_at": source.removed_at.isoformat() if source.removed_at else None,
            "source_kind": source.metadata.get("source_kind")
            or ("document_upload" if kind == "upload" else "text"),
        }

    async def inventory(self, kb: str, offset: int = 0, *, include_removed: bool = False) -> dict[str, Any]:
        """Return one source page with chunk counts and each latest job."""

        knowledge_base_id, spec = self.spec(kb)
        rows, total = await self.sources.list_page(
            knowledge_base_id, limit=PAGE_SIZE, offset=offset, include_removed=include_removed
        )
        jobs = await self.jobs.latest(knowledge_base_id, [row.id for row in rows])
        return {
            "sources": [self._summary(kb, row, jobs.get(row.id)) for row in rows],
            "warnings": [],
            "next_offset": offset + PAGE_SIZE if offset + len(rows) < total else None,
            "qa_mode": spec.retrieval.value,
        }

    async def remove_source(
        self, kb: str, source_id: UUID, *, actor_id: UUID, role: str,
    ) -> None:
        """Remove one source if the actor is allowed and it has no running job."""

        knowledge_base_id, _spec = self.spec(kb)
        try:
            removed = await self.sources.remove(
                knowledge_base_id, source_id, actor_id=actor_id, role=role,
            )
        except SourceRemovalForbidden as exc:
            raise HTTPException(403, "Only the submitter, an expert or an administrator may remove this source") from exc
        except SourceJobRunning as exc:
            raise HTTPException(409, "A job is running for this source") from exc
        if not removed:
            raise HTTPException(404, "Source not found")

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
        references = (
            await self.chunks.references_for_source(knowledge_base_id, identifier)
            if hasattr(self.chunks, "references_for_source")
            else {}
        )
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
        serialized_records = []
        for record in records:
            value = record_json(record)
            value["references"] = references.get(record.id, [{"type": "source"}])
            serialized_records.append(value)
        return {
            **summary,
            "text": text[:PREVIEW_CHARS] if text is not None else None,
            "text_truncated": text is not None and len(text) > PREVIEW_CHARS,
            "records": serialized_records,
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

    @staticmethod
    def _source_info(run: Any, source: SourceEntry | None) -> dict[str, Any]:
        name = source.title if source is not None else str(run.source_id)
        kind = "upload" if source is not None and source.file is not None else "conversation"
        return {
            "run_id": str(run.id),
            "evidence_id": str(run.source_id),
            "status": run.status,
            "mode": run.mode,
            "name": name,
            "kind": kind,
            "source_id": str(run.source_id),
            "extracted_at": (run.finished_at or run.created_at).isoformat(),
        }

    async def _duplicate(self, knowledge_base_id: UUID, run: Any) -> dict[str, Any]:
        source = await self.sources.get(knowledge_base_id, run.source_id)
        return {**self._source_info(run, source), "same_logic": None}

    async def _overlaps(
        self,
        knowledge_base_id: UUID,
        mode: str,
        text: str,
        *,
        exclude: UUID,
    ) -> list[dict[str, Any]]:
        """Return extracted sources whose signed text overlaps the new source."""

        if self.sessions is None:
            return []
        new = await asyncio.to_thread(signature, text)
        runs = ExtractionRunRepository(self.sessions, knowledge_base_id)
        candidates = await runs.overlap_candidates(
            new.bands,
            mode,
            version=SIGNATURE_VERSION,
            content=content_sha256(text),
            exclude=exclude,
            limit=OVERLAP_CANDIDATES,
        )
        matches = []
        for run, _hits in candidates:
            source = await self.sources.get(knowledge_base_id, run.source_id)
            if source is None or source.extracted_text is None:
                continue
            scores = overlap(
                new.shingles,
                await asyncio.to_thread(shingles, source.extracted_text),
            )
            level = self.thresholds.level(scores)
            if level:
                matches.append({
                    **self._source_info(run, source),
                    "level": level,
                    "jaccard": round(scores.jaccard, 3),
                    "containment": round(scores.containment, 3),
                    "coverage": round(scores.coverage, 3),
                })
        matches.sort(
            key=lambda match: (
                match["level"] not in CONFIRM_LEVELS,
                -max(match["jaccard"], match["containment"], match["coverage"]),
            )
        )
        return matches[:MAX_OVERLAPS]

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

        knowledge_base_id, spec = self.spec(kb)
        identifier = UUID(source_id)
        source = await self.sources.get(knowledge_base_id, identifier)
        if source is None:
            raise HTTPException(404, "Source not found")
        if await self.chunks.has_results(knowledge_base_id, identifier, mode):
            raise HTTPException(409, "This source already has extracted results")
        if mode == "knowledge" and source.extracted_text is not None and len(source.extracted_text) > MAX_TEXT_CHARS:
            raise HTTPException(422, f"Knowledge extraction accepts at most {MAX_TEXT_CHARS} characters")
        if self.sessions is not None:
            runs = ExtractionRunRepository(self.sessions, knowledge_base_id)
            earlier = None
            if source.extracted_text is None and source.file is not None:
                earlier = await runs.find_active_original(source.file.sha256, mode)
            elif source.extracted_text is not None:
                earlier = await runs.find_active(
                    content_sha256(source.extracted_text), mode, exclude=identifier
                )
            if earlier is not None:
                duplicate = await self._duplicate(knowledge_base_id, earlier)
                raise HTTPException(409, {
                    "code": "duplicate_content",
                    "message": duplicate_message(duplicate),
                    "duplicate_of": duplicate,
                })
            if source.extracted_text is not None and not overlap_in_worker:
                overlaps = await self._overlaps(
                    knowledge_base_id, mode, source.extracted_text, exclude=identifier
                )
                if not confirm_overlap and any(
                    match["level"] in CONFIRM_LEVELS for match in overlaps
                ):
                    raise HTTPException(409, {
                        "code": "near_duplicate",
                        "message": overlap_message(overlaps),
                        "matches": overlaps,
                    })
            else:
                overlaps = []
        else:
            overlaps = []
        options = {"confirm_overlap": True} if confirm_overlap else None
        stage = "extract" if source.extracted_text is not None else "convert"
        if stage == "convert" and not self._convertible(source):
            raise HTTPException(409, NO_CONVERTER)
        conflict_stages = (
            ("conflict",)
            if mode == "knowledge" and spec.conflict_factory is not None
            else ()
        )
        next_stages = (("extract", *conflict_stages) if stage == "convert" else conflict_stages)
        queued = await self.enqueue(kb, stage, identifier, mode=mode, next_stages=next_stages, options=options)
        return {**queued, "overlaps": overlaps}

    async def review_record(self, kb: str, record_id: UUID, decision: str, note: str, reviewer_id: UUID) -> dict[str, Any]:
        """Review a knowledge record as the signed-in expert."""

        knowledge_base_id, _spec = self.spec(kb)
        status = KnowledgeStatus(DECISIONS[decision])
        event = {
            "action": "reviewed", "decision": decision, "status": status.value,
            "note": note.strip() or None, "actor": str(reviewer_id),
            "at": datetime.now(timezone.utc).isoformat(),
        }
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
            "at": datetime.now(timezone.utc).isoformat(),
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

    async def queue_conflicts(self, kb: str, evidence_id: UUID) -> None:
        """Queue conflict detection after another application extraction path."""

        _knowledge_base_id, spec = self.spec(kb)
        if spec.conflict_factory is None:
            return
        try:
            await self.enqueue(kb, "conflict", evidence_id, mode="knowledge")
        except HTTPException as exc:
            if exc.status_code != 409:
                raise

    async def conflict_list(
        self,
        kb: str,
        *,
        status: str | None = None,
        evidence_id: UUID | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return current application conflict rows and their knowledge records."""

        knowledge_base_id, spec = self.spec(kb)
        page = {
            "conflicts": [], "counts": {}, "offset": offset,
            "limit": CONFLICT_PAGE_SIZE, "next_offset": None,
        }
        if spec.conflict_factory is None or self.sessions is None:
            return {**page, "enabled": False}
        if status is not None and status not in CONFLICT_STATUSES:
            raise HTTPException(422, "Unknown conflict status")
        record_ids: list[UUID] = []
        if evidence_id is not None:
            record_ids = [
                record.id for record in await self.chunks.records_for_source(
                    knowledge_base_id, evidence_id
                )
            ]
            if not record_ids:
                return {**page, "enabled": True}
        conflicts = ConflictRepository(self.sessions, knowledge_base_id)
        rows, counts = await conflicts.list_rows(
            statuses=(status,) if status else (),
            record_ids=record_ids,
            limit=CONFLICT_PAGE_SIZE + 1,
            offset=offset,
        )
        more = len(rows) > CONFLICT_PAGE_SIZE
        rows = rows[:CONFLICT_PAGE_SIZE]
        records = {
            record.id: record
            for record in await self.chunks.get_records(
                knowledge_base_id,
                list(dict.fromkeys(record_id for row in rows for record_id in row.pair)),
            )
        }
        return {
            **page,
            "enabled": True,
            "counts": counts,
            "next_offset": offset + CONFLICT_PAGE_SIZE if more else None,
            "conflicts": [self._conflict_json(row, records) for row in rows],
        }

    @staticmethod
    def _conflict_json(row: Any, records: dict[UUID, Any]) -> dict[str, Any]:
        def side(record_id: UUID) -> dict[str, Any]:
            record = records.get(record_id)
            if record is None:
                return {"id": str(record_id), "missing": True}
            return {
                **record_json(record),
                "evidence_id": str(record.evidence_id) if record.evidence_id else None,
                "quote": row.quotes.get(str(record_id)) or None,
            }

        return {
            **row.to_json(),
            "stale": stale(row, records),
            "a": side(row.a_id),
            "b": side(row.b_id),
        }

    async def review_conflict(
        self,
        kb: str,
        conflict_id: UUID,
        decision: str,
        note: str,
        keep_id: UUID | None = None,
        reviewer_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Store a conflict decision without approving either knowledge record."""

        knowledge_base_id, spec = self.spec(kb)
        if spec.conflict_factory is None or self.sessions is None:
            raise HTTPException(404, "This knowledge base has no conflict detection")
        conflicts = ConflictRepository(self.sessions, knowledge_base_id)
        row = await conflicts.get_row(conflict_id)
        if row is None:
            raise HTTPException(404, "Conflict not found")
        current = (await conflicts.current([row.pair])).get(row.pair)
        if current is not None and current.id != row.id:
            raise HTTPException(409, "A newer judgement of this pair exists; reload the list")
        if row.status == "resolved":
            raise HTTPException(409, "This conflict was already resolved")
        records = {
            record.id: record
            for record in await self.chunks.get_records(knowledge_base_id, list(row.pair))
        }
        if len(records) < 2:
            raise HTTPException(409, "A record of this pair no longer exists")
        first, second = records[row.a_id], records[row.b_id]
        status = CONFLICT_DECISIONS[decision]
        event = {
            "action": "conflict_reviewed",
            "decision": decision,
            "status": status,
            "note": note.strip() or None,
            "actor": str(reviewer_id) if reviewer_id else None,
            "at": datetime.now(timezone.utc).isoformat(),
            "conflict_id": str(row.id),
            "pair": [str(row.a_id), str(row.b_id)],
        }
        linked = row.linked
        if decision == "supersede":
            if keep_id not in records:
                raise HTTPException(422, "keep_id must be one of the two records")
            retired = second if keep_id == first.id else first
            if retired.status is KnowledgeStatus.DEPRECATED or retired.superseded_by is not None:
                raise HTTPException(409, "The other record is already retired")
            event = {**event, "kept": str(keep_id), "retired": str(retired.id)}
            updates = [
                KnowledgeLinkUpdate(retired.id, superseded_by=keep_id, event=event),
                KnowledgeLinkUpdate(keep_id, event=event),
            ]
        else:
            linked = decision == "confirm"
            updates = link_updates(first, second, records, linked=linked, event=event)
            touched = {update.record_id for update in updates}
            updates += [
                KnowledgeLinkUpdate(record_id, event=event)
                for record_id in row.pair if record_id not in touched
            ]
        await self.chunks.apply_updates(knowledge_base_id, updates)
        reviewed = await conflicts.set_review(
            row.id, status, linked=linked, event=event
        )
        refreshed = {
            record.id: record
            for record in await self.chunks.get_records(knowledge_base_id, list(row.pair))
        }
        return self._conflict_json(reviewed, refreshed)

    async def recheck_conflicts(
        self, kb: str, record_ids: Any = ()
    ) -> dict[str, Any]:
        """Queue conflict jobs for sources of edited or stale knowledge records."""

        knowledge_base_id, spec = self.spec(kb)
        if spec.conflict_factory is None or self.sessions is None:
            raise HTTPException(404, "Conflict detection is not available")
        conflicts = ConflictRepository(self.sessions, knowledge_base_id)
        evidence: dict[UUID, None] = {}
        stale_rows = 0
        if record_ids:
            records = await self.chunks.get_records(knowledge_base_id, list(record_ids))
            if not records:
                raise HTTPException(404, "Knowledge records not found")
            evidence.update({record.evidence_id: None for record in records if record.evidence_id})
        else:
            rows, _counts = await conflicts.list_rows(
                statuses=CLAIMING_STATUSES, limit=RECHECK_SCAN_LIMIT
            )
            records = {
                record.id: record
                for record in await self.chunks.get_records(
                    knowledge_base_id,
                    list(dict.fromkeys(record_id for row in rows for record_id in row.pair)),
                )
            }
            for row in rows:
                changed = [
                    record_id for record_id in row.pair
                    if record_id in records and stale(row, {record_id: records[record_id]})
                ]
                if changed and records[changed[0]].evidence_id is not None:
                    stale_rows += 1
                    evidence[records[changed[0]].evidence_id] = None
        queued, busy = [], []
        for source_id in evidence:
            try:
                job = await self.jobs.enqueue(
                    knowledge_base_id, source_id, "conflict", mode="knowledge"
                )
            except JobConflict:
                busy.append(str(source_id))
                continue
            queued.append(job.to_json())
        return {
            "queued": queued,
            "busy": busy,
            "sources": len(evidence),
            "stale": stale_rows,
        }

    async def aclose(self) -> None:
        """Repositories own no persistent connection resources."""

