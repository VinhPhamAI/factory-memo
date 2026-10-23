"""Inventory, processing requests and review of source evidence.

Processing runs in the job workers (`backend.worker`): this service validates
a request, queues the first stage, and reports each source's latest job next
to the state its handlers wrote.
"""

from __future__ import annotations

import base64
import logging
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from backend.api.voice_capture import VoiceCaptureService, default_ingestion_factory, record_json
from backend.src.services.source_pipeline import (
    NO_CONVERTER, SourcePipeline, duplicate_message, open_documents, open_runs, overlap_message,
)
from backend.src.storage.content_signatures import CONFIRM_LEVELS
from backend.src.services.voice_capture import CAPTURE_SOURCE_KINDS, MAX_TEXT_CHARS
from backend.src.storage import KnowledgeStore, StorageError
from backend.src.storage.document_uploads import DocumentUploadStore, text_convertible
from backend.src.storage.extraction_runs import content_sha256
from backend.src.storage.job_queue import Job, JobConflict, JobQueue
from backend.src.storage.models import ConversationStatus, KnowledgeStatus


PAGE_SIZE = 100
PREVIEW_CHARS = 100_000
DECISIONS = {"approve": "approved", "reject": "deprecated", "reset": "draft"}
logger = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def document_token(doc_id: str) -> str:
    return base64.urlsafe_b64encode(doc_id.encode()).decode().rstrip("=")


def document_id(token: str) -> str:
    try:
        value = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode()
        if not value or document_token(value) != token:
            raise ValueError()
        return value
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(404, "Document source not found") from exc


def review_state(counts: dict[str, int], extracted: bool) -> str:
    if not extracted or not sum(counts.values()):
        return "none"
    if counts.get("draft", 0):
        return "pending"
    if counts.get("deprecated", 0) and not counts.get("approved", 0):
        return "rejected"
    return "reviewed"


def job_state(state, job: Job | None, *, legacy_running=False, known=True):
    """Processing state of a source: the handlers' record, overlaid by its latest job."""

    state = dict(state)
    if job is not None:
        state["job"] = job.to_json()
        if job.active:
            state.update(status=job.status, mode=job.mode or state.get("mode"), error=None)
            return state
        if job.status == "failed" and state.get("status") != "done":
            state.update(status="failed", mode=job.mode or state.get("mode"), error=job.error)
            return state
    # A run whose job is gone (or a legacy in-process run that stopped with the API).
    if state.get("status") in {"queued", "running"} and not legacy_running and known:
        state["status"] = "interrupted"
    return state


class KnowledgeManagementService(SourcePipeline):
    def __init__(self, specs, uploads: DocumentUploadStore, capture: VoiceCaptureService, *,
                 queue=None, open_store=KnowledgeStore.for_kb, open_document_store=open_documents,
                 ingestion_factory=default_ingestion_factory, open_runs=open_runs, thresholds=None):
        super().__init__(specs, uploads, open_store=open_store, open_document_store=open_document_store,
                         ingestion_factory=ingestion_factory, open_runs=open_runs, thresholds=thresholds)
        self.capture = capture
        self.queue = queue if queue is not None else JobQueue()

    def spec(self, kb):
        if kb not in self.specs:
            raise HTTPException(404, "Knowledge base not found")
        return self.specs[kb]

    async def jobs(self, kb, sources=None) -> tuple[dict, bool]:
        """Latest job per source, and whether the queue could be read."""

        try:
            return await self.queue.latest(kb, sources), True
        except StorageError as exc:
            logger.warning("Could not read processing jobs for %s: %s", kb, exc)
        except Exception:
            logger.warning("Could not read processing jobs for %s", kb, exc_info=True)
        return {}, False

    def processing(self, kb, row, job=None, known=True):
        state = row.metadata.get("management") or row.metadata.get("ingestion") or {}
        return job_state(state, job, legacy_running=bool(self.capture.running(row.id, "ingestion")), known=known)

    def _summary(self, *, kb, kind, source_id, name, created_at, content_type,
                 converted, extracted, counts, mode=None, processing=None,
                 can_convert=False, conversion_note=None, source_kind=None):
        return {
            "id": source_id, "kind": kind, "kb": kb, "name": name,
            "created_at": created_at, "content_type": content_type,
            "stage": "extracted" if extracted else "converted" if converted else "raw",
            "mode": mode, "review": review_state(counts, extracted), "counts": counts,
            "processing": processing or {}, "can_convert": can_convert,
            "conversion_note": conversion_note, "source_kind": source_kind,
            "can_extract_raw": False,
        }

    async def inventory(self, kb, offset=0):
        spec = self.spec(kb)
        items, warnings = [], []
        jobs, known = await self.jobs(kb)
        if not known:
            warnings.append("Không đọc được hàng đợi xử lý; trạng thái job có thể chưa cập nhật.")
        uploads = await run_in_threadpool(self.uploads.list, kb)
        linked_ids = set()
        for upload in uploads:
            state = await run_in_threadpool(self.uploads.processing, kb, upload.id)
            if state.get("evidence_id"):
                linked_ids.add(state["evidence_id"])
            if offset == 0:
                items.append(await self.upload_summary(kb, upload, state, jobs.get(("upload", upload.id)), known))
        rows, docs = [], []
        try:
            store = await self.store(kb)
            rows = await store.list_conversations(
                metadata_filters=[{}], metadata_keys=["name", "filename", "source_kind", "management", "ingestion"],
                limit=PAGE_SIZE, offset=offset,
            )
            for row in rows:
                if str(row["id"]) in linked_ids:
                    continue
                # List rows omit the transcript; it is loaded only in the detail view.
                metadata = row["metadata"]
                state = job_state(metadata.get("management") or metadata.get("ingestion") or {},
                                  jobs.get(("conversation", str(row["id"]))),
                                  legacy_running=bool(self.capture.running(row["id"], "ingestion")), known=known)
                counts = dict(row["knowledge_counts"])
                if state.get("mode") == "documents":
                    chunks = await (await self.documents(kb)).get_document(str(row["id"]))
                    counts = dict(Counter(chunk.metadata.get("review_status", "draft") for chunk in chunks))
                extracted = row["status"] == "processed" or state.get("status") == "done" or bool(sum(counts.values()))
                items.append(self._summary(
                    kb=kb, kind="conversation", source_id=str(row["id"]),
                    name=metadata.get("name") or metadata.get("filename") or str(row["id"]),
                    created_at=row["created_at"].isoformat(), content_type="text/plain",
                    converted=True, extracted=extracted, counts=counts,
                    mode=state.get("mode") or ("knowledge" if counts else spec.retrieval.value),
                    processing=state, source_kind=metadata.get("source_kind", "text"),
                ))
        except Exception:
            warnings.append("Không tải được danh sách nguồn từ cơ sở dữ liệu. File gốc vẫn được giữ trong kho lưu trữ.")
        try:
            docs = await (await self.documents(kb)).document_summaries(limit=PAGE_SIZE, offset=offset)
            for doc in docs:
                # Managed chunks are already shown under their upload/transcript source.
                if doc.get("evidence_id"):
                    continue
                items.append(self._summary(
                    kb=kb, kind="document", source_id=document_token(doc["doc_id"]),
                    name=doc["title"] or doc["doc_id"], created_at=doc.get("created_at"),
                    content_type="text/plain", converted=True, extracted=True,
                    counts={key: int(doc[key]) for key in ("draft", "approved", "deprecated")},
                    mode="documents", source_kind="document_index",
                ))
        except Exception:
            warnings.append("Không tải được danh sách document chunks. Hãy thử làm mới.")
        items.sort(key=lambda item: (item.get("created_at") or "", item["id"]), reverse=True)
        return {"sources": items, "warnings": warnings,
                "next_offset": offset + PAGE_SIZE if len(rows) == PAGE_SIZE or len(docs) == PAGE_SIZE else None,
                "qa_mode": spec.retrieval.value}

    async def upload_summary(self, kb, upload, state, job=None, known=True):
        converted = state.get("conversion", {}).get("status") == "done"
        processing, counts, extracted = job_state({}, job, known=known), {}, False
        if state.get("evidence_id"):
            try:
                detail = await self.evidence_detail(kb, UUID(state["evidence_id"]), job=job, known=known)
                processing, counts = detail["processing"], detail["counts"]
                extracted = detail["stage"] == "extracted"
            except Exception:
                processing = {"status": "unavailable", "error": "Không đọc được kết quả xử lý từ cơ sở dữ liệu."}
        convertible = text_convertible(upload)
        summary = self._summary(
            kb=kb, kind="upload", source_id=upload.id, name=upload.filename,
            created_at=upload.uploaded_at.isoformat(), content_type=upload.content_type,
            converted=converted, extracted=extracted, counts=counts,
            mode=processing.get("mode"), processing=processing,
            can_convert=not converted and convertible,
            conversion_note=None if converted or convertible else NO_CONVERTER,
            source_kind="document_upload",
        )
        # Extract on a raw file queues conversion first, then extraction.
        summary["can_extract_raw"] = not converted and convertible
        return summary

    async def evidence_detail(self, kb, evidence_id, *, job=None, known=None):
        store = await self.store(kb)
        row = await store.get_conversation(evidence_id)
        if row is None:
            raise HTTPException(404, "Source not found")
        if known is None:
            jobs, known = await self.jobs(kb, [("conversation", str(row.id))])
            job = jobs.get(("conversation", str(row.id)))
        state = self.processing(kb, row, job, known)
        mode = state.get("mode") or "knowledge"
        records = await store.knowledge_for_evidence(row.id)
        chunks = await (await self.documents(kb)).get_document(str(row.id)) if mode == "documents" else []
        counts = Counter(record.status.value for record in records)
        counts.update(Counter(chunk.metadata.get("review_status", "draft") for chunk in chunks))
        extracted = row.status is ConversationStatus.PROCESSED or state.get("status") == "done" or bool(records or chunks)
        detail = self._summary(
            kb=kb, kind="conversation", source_id=str(row.id), name=row.metadata.get("name") or str(row.id),
            created_at=row.created_at.isoformat(), content_type="text/plain", converted=True,
            extracted=extracted, counts=counts, mode=mode, processing=state,
            source_kind=row.metadata.get("source_kind", "text"),
        )
        detail.update(text=row.transcript[:PREVIEW_CHARS], text_truncated=len(row.transcript) > PREVIEW_CHARS,
                      records=[record_json(record) for record in records],
                      chunks=[self.chunk_json(chunk) for chunk in chunks], evidence_id=str(row.id),
                      conversation=None)
        if row.metadata.get("source_kind") in CAPTURE_SOURCE_KINDS and kb in {x["name"] for x in self.capture.knowledge_bases()}:
            detail["conversation"] = await self.capture.detail(kb, row.id)
        return detail

    @staticmethod
    def chunk_json(chunk):
        return {"id": chunk.chunk_id, "text": chunk.text, "title": chunk.title,
                "status": chunk.metadata.get("review_status", "draft"),
                "history": chunk.metadata.get("review_history", []),
                "doc_id": chunk.doc_id, "index": chunk.chunk_index}

    async def detail(self, kb, kind, source_id):
        self.spec(kb)
        if kind == "conversation":
            return await self.evidence_detail(kb, UUID(source_id))
        if kind == "document":
            chunks = await (await self.documents(kb)).get_document(document_id(source_id))
            if not chunks:
                raise HTTPException(404, "Document source not found")
            detail = self._summary(
                kb=kb, kind=kind, source_id=source_id, name=chunks[0].title or chunks[0].doc_id,
                created_at=None, content_type="text/plain", converted=True, extracted=True,
                counts=dict(Counter(chunk.metadata.get("review_status", "draft") for chunk in chunks)),
                mode="documents", source_kind="document_index",
            )
            return {**detail, "text": None, "records": [], "chunks": [self.chunk_json(c) for c in chunks],
                    "conversation": None, "conversion_note": "Chỉ có chunks đã lập chỉ mục; bản gốc không nằm trong kho tài liệu tải lên."}
        upload = await run_in_threadpool(self.uploads.get, kb, source_id)
        state = await run_in_threadpool(self.uploads.processing, kb, source_id)
        jobs, known = await self.jobs(kb, [("upload", source_id)])
        summary = await self.upload_summary(kb, upload, state, jobs.get(("upload", source_id)), known)
        if state.get("evidence_id"):
            detail = await self.evidence_detail(kb, UUID(state["evidence_id"]),
                                                job=jobs.get(("upload", source_id)), known=known)
            return {**detail, **summary}
        text = await run_in_threadpool(self.uploads.converted_text, kb, source_id)
        return {**summary, "text": text[:PREVIEW_CHARS] if text is not None else None,
                "text_truncated": text is not None and len(text) > PREVIEW_CHARS,
                "records": [], "chunks": [], "conversation": None}

    # ----------------------------------------------------------- requests

    async def enqueue(self, kb, stage, kind, source_id, *, mode=None, next_stages=(), options=None):
        try:
            job = await self.queue.enqueue(kb, stage, kind, source_id, mode=mode, next_stages=next_stages,
                                           options=options)
        except JobConflict as exc:
            raise HTTPException(409, "This source is already being processed") from exc
        return {"job": job.to_json()}

    async def convert(self, kb, source_id):
        """Queues text conversion of an uploaded original (raw -> converted)."""

        self.spec(kb)
        upload = await run_in_threadpool(self.uploads.get, kb, source_id)
        state = await run_in_threadpool(self.uploads.processing, kb, source_id)
        if state.get("conversion", {}).get("status") == "done":
            raise HTTPException(409, "This file is already converted")
        if not text_convertible(upload):
            raise HTTPException(422, NO_CONVERTER)
        return await self.enqueue(kb, "convert", "upload", source_id)

    async def check_duplicate(self, kb, mode, *, text=None, original=None, exclude=None):
        """409 when this exact content (or upload bytes) was already extracted in this mode.

        The worker checks again atomically when the job runs; this check only
        spares the reviewer a job that would be skipped.
        """

        await self.backfill(kb)
        runs = await self.runs(kb)
        if text is not None:
            run = await runs.find_active(content_sha256(text), mode, exclude=exclude)
        else:
            run = await runs.find_active_original(original, mode)
        if run is None:
            return
        spec, store = self.spec(kb), await self.store(kb)
        duplicate = await self.duplicate_info(kb, run, self.current_fingerprint(spec, store, mode))
        raise HTTPException(409, {"code": "duplicate_content", "message": duplicate_message(duplicate),
                                  "duplicate_of": duplicate})

    async def check_overlap(self, kb, mode, text, *, confirmed, exclude=None, deferred=False):
        """409 when the text nearly matches an extracted source, unless the reviewer confirmed.

        Returns the matches to show next to the queued job (information only,
        or confirmed). The worker checks again when the job runs; `deferred`
        leaves the check to it, so the source records the warning (Auto mode).
        """

        if deferred:
            return []
        matches = await self.overlaps(kb, mode, text, exclude=exclude)
        if not confirmed and any(match["level"] in CONFIRM_LEVELS for match in matches):
            raise HTTPException(409, {"code": "near_duplicate", "message": overlap_message(matches),
                                      "matches": matches})
        return matches

    async def extract(self, kb, kind, source_id, mode, *, confirm_overlap=False, overlap_in_worker=False):
        """Queues extraction; a raw upload is converted first, in the same pipeline.

        Content that nearly matches an extracted source needs `confirm_overlap`;
        the option goes with the job (and its next stages) to the worker. With
        `overlap_in_worker` (Auto mode, nobody to confirm now) the job is queued
        and the worker marks the source `near_duplicate`.
        """

        self.spec(kb)
        options = {"confirm_overlap": True} if confirm_overlap else None
        if kind == "document":
            raise HTTPException(409, "This source is already extracted")
        store = await self.store(kb)
        row = None
        if kind == "upload":
            upload = await run_in_threadpool(self.uploads.get, kb, source_id)
            state = await run_in_threadpool(self.uploads.processing, kb, source_id)
            if state.get("evidence_id"):
                row = await store.get_conversation(UUID(state["evidence_id"]))
            if row is None:
                text = await run_in_threadpool(self.uploads.converted_text, kb, source_id)
                if text is None:
                    if not text_convertible(upload):
                        raise HTTPException(409, NO_CONVERTER)
                    await self.check_duplicate(kb, mode, original=upload.sha256)
                    # The text is not known before conversion; the worker checks overlaps.
                    return await self.enqueue(kb, "convert", "upload", source_id, mode=mode,
                                              next_stages=("extract",), options=options)
                if mode == "knowledge" and len(text) > MAX_TEXT_CHARS:
                    raise HTTPException(422, f"Knowledge extraction accepts at most {MAX_TEXT_CHARS} characters")
                await self.check_duplicate(kb, mode, text=text)
                overlaps = await self.check_overlap(kb, mode, text, confirmed=confirm_overlap,
                                                    deferred=overlap_in_worker)
                return {**await self.enqueue(kb, "extract", "upload", source_id, mode=mode, options=options),
                        "overlaps": overlaps}
        else:
            row = await store.get_conversation(UUID(source_id))
            if row is None:
                raise HTTPException(404, "Source not found")
            if self.capture.running(row.id, "ingestion"):
                raise HTTPException(409, "Extraction is already running")
        if await self.has_results(kb, row):
            raise HTTPException(409, "This source already has extracted results; review them first")
        if mode == "knowledge" and len(row.transcript) > MAX_TEXT_CHARS:
            raise HTTPException(422, f"Knowledge extraction accepts at most {MAX_TEXT_CHARS} characters")
        await self.check_duplicate(kb, mode, text=row.transcript, exclude=row.id)
        overlaps = await self.check_overlap(kb, mode, row.transcript, confirmed=confirm_overlap, exclude=row.id,
                                            deferred=overlap_in_worker)
        return {**await self.enqueue(kb, "extract", kind, source_id, mode=mode, options=options),
                "overlaps": overlaps}

    async def review_record(self, kb, record_id, decision, note):
        status = DECISIONS[decision]
        record = await (await self.store(kb)).review_knowledge(
            record_id, KnowledgeStatus(status), event=self.review_event(decision, note),
        )
        if record is None:
            raise HTTPException(404, "Knowledge record not found, or superseded")
        return record_json(record)

    @staticmethod
    def review_event(decision, note):
        return {"action": "reviewed", "decision": decision, "status": DECISIONS[decision],
                "note": note.strip() or None, "actor": "knowledge management UI (unauthenticated)", "at": now()}

    async def review_chunk(self, kb, kind, source_id, chunk_id, decision, note):
        detail = await self.detail(kb, kind, source_id)
        chunk = next((chunk for chunk in detail["chunks"] if chunk["id"] == chunk_id), None)
        if chunk is None:
            raise HTTPException(404, "Chunk not found for this source")
        documents = await self.documents(kb)
        if not await documents.review_chunk(chunk["doc_id"], chunk_id, DECISIONS[decision],
                                            event=self.review_event(decision, note)):
            raise HTTPException(404, "Chunk not found")
        return await self.detail(kb, kind, source_id)

    async def aclose(self):
        await super().aclose()
        await self.queue.aclose()
