"""Inventory and processing of source evidence, independently of its capture UI."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from backend.api.voice_capture import VoiceCaptureService, default_ingestion_factory, record_json
from backend.src.agents.rag import Document, DocumentStore, chunk_document
from backend.src.agents.rag.indexing import index_config
from backend.src.services.knowledge_ingestion import transcript_sha256
from backend.src.services.voice_capture import CAPTURE_SOURCE_KINDS, MAX_TEXT_CHARS
from backend.src.storage import KnowledgeStore
from backend.src.storage.document_uploads import DocumentUploadStore, text_convertible
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


async def open_documents(spec):
    return await DocumentStore.for_database(
        spec.database, table=spec.document_table,
        text_search_config=spec.text_search_config,
        query_instruction=spec.embedding_query_instruction,
    )


class KnowledgeManagementService:
    def __init__(self, specs, uploads: DocumentUploadStore, capture: VoiceCaptureService, *,
                 open_store=KnowledgeStore.for_kb, open_document_store=open_documents,
                 ingestion_factory=default_ingestion_factory):
        self.specs = dict(specs)
        self.uploads = uploads
        self.capture = capture
        self._open_store = open_store
        self._open_documents = open_document_store
        self._ingestion_factory = ingestion_factory
        self._stores: dict[str, Any] = {}
        self._documents: dict[str, Any] = {}
        self._jobs: dict[tuple[str, str], asyncio.Task] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._store_lock = asyncio.Lock()

    def add(self, spec):
        self.specs[spec.name] = spec

    def spec(self, kb):
        if kb not in self.specs:
            raise HTTPException(404, "Knowledge base not found")
        return self.specs[kb]

    async def store(self, kb):
        spec = self.spec(kb)
        async with self._store_lock:
            if kb not in self._stores:
                self._stores[kb] = await self._open_store(spec)
        return self._stores[kb]

    async def documents(self, kb):
        spec = self.spec(kb)
        async with self._store_lock:
            if kb not in self._documents:
                self._documents[kb] = await self._open_documents(spec)
        return self._documents[kb]

    def lock(self, kb, source_id):
        return self._locks.setdefault((kb, source_id), asyncio.Lock())

    def processing(self, kb, row):
        state = dict(row.metadata.get("management") or row.metadata.get("ingestion") or {})
        running = (kb, str(row.id)) in self._jobs or self.capture.running(row.id, "ingestion")
        if state.get("status") in {"queued", "running"} and not running:
            state["status"] = "interrupted"
        return state

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
        uploads = await run_in_threadpool(self.uploads.list, kb)
        linked_ids = set()
        for upload in uploads:
            state = await run_in_threadpool(self.uploads.processing, kb, upload.id)
            if state.get("evidence_id"):
                linked_ids.add(state["evidence_id"])
            if offset == 0:
                items.append(await self.upload_summary(kb, upload, state))
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
                state = dict(metadata.get("management") or metadata.get("ingestion") or {})
                running = (kb, str(row["id"])) in self._jobs or self.capture.running(row["id"], "ingestion")
                if state.get("status") in {"queued", "running"} and not running:
                    state["status"] = "interrupted"
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

    async def upload_summary(self, kb, upload, state):
        converted = state.get("conversion", {}).get("status") == "done"
        processing, counts, extracted = {}, {}, False
        if state.get("evidence_id"):
            try:
                detail = await self.evidence_detail(kb, UUID(state["evidence_id"]))
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
            conversion_note=None if converted or convertible else "Chưa có OCR/bộ chuyển đổi cho loại file này. File gốc đã được lưu.",
            source_kind="document_upload",
        )
        summary["can_extract_raw"] = not converted and Path(upload.filename).suffix.lower() == ".txt"
        return summary

    async def evidence_detail(self, kb, evidence_id):
        store = await self.store(kb)
        row = await store.get_conversation(evidence_id)
        if row is None:
            raise HTTPException(404, "Source not found")
        state = self.processing(kb, row)
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
        summary = await self.upload_summary(kb, upload, state)
        if state.get("evidence_id"):
            detail = await self.evidence_detail(kb, UUID(state["evidence_id"]))
            return {**detail, **summary}
        text = await run_in_threadpool(self.uploads.converted_text, kb, source_id)
        return {**summary, "text": text[:PREVIEW_CHARS] if text is not None else None,
                "text_truncated": text is not None and len(text) > PREVIEW_CHARS,
                "records": [], "chunks": [], "conversation": None}

    async def convert(self, kb, source_id):
        self.spec(kb)
        async with self.lock(kb, "upload:" + source_id):
            await run_in_threadpool(self.uploads.convert_text, kb, source_id)
        return await self.detail(kb, "upload", source_id)

    async def extract(self, kb, kind, source_id, mode):
        self.spec(kb)
        if kind == "document":
            raise HTTPException(409, "This source is already extracted")
        async with self.lock(kb, kind + ":" + source_id):
            store = await self.store(kb)
            if kind == "upload":
                upload = await run_in_threadpool(self.uploads.get, kb, source_id)
                state = await run_in_threadpool(self.uploads.processing, kb, source_id)
                text = await run_in_threadpool(self.uploads.converted_text, kb, source_id)
                if text is None and Path(upload.filename).suffix.lower() == ".txt":
                    state = await run_in_threadpool(self.uploads.convert_text, kb, source_id)
                    text = await run_in_threadpool(self.uploads.converted_text, kb, source_id)
                if text is None:
                    raise HTTPException(409, "Convert the original file to text first")
                if state.get("evidence_id"):
                    row = await store.get_conversation(UUID(state["evidence_id"]))
                else:
                    row = await store.add_conversation(text, metadata={
                        "source_kind": "document_upload", "upload_id": upload.id,
                        "name": upload.filename, "filename": upload.filename,
                        "original_sha256": upload.sha256, "storage_key": upload.storage_key,
                        "transcript_sha256": transcript_sha256(text),
                    })
                    state["evidence_id"] = str(row.id)
                    await run_in_threadpool(self.uploads.update_processing, kb, source_id, state)
            else:
                row = await store.get_conversation(UUID(source_id))
            if row is None:
                raise HTTPException(404, "Source not found")
            key = (kb, str(row.id))
            if key in self._jobs or self.capture.running(row.id, "ingestion"):
                raise HTTPException(409, "Extraction is already running")
            previous = row.metadata.get("management") or {}
            if (row.status is ConversationStatus.PROCESSED
                or await store.knowledge_for_evidence(row.id)
                or (previous.get("mode") == "documents" and await (await self.documents(kb)).get_document(str(row.id)))):
                raise HTTPException(409, "This source already has extracted results; review them first")
            if mode == "knowledge" and len(row.transcript) > MAX_TEXT_CHARS:
                raise HTTPException(422, f"Knowledge extraction accepts at most {MAX_TEXT_CHARS} characters")
            await store.update_conversation_metadata(row.id, {"management": {"status": "queued", "mode": mode}})
            task = asyncio.create_task(self._extract(kb, row, mode))
            self._jobs[key] = task
            def finished(task):
                self._jobs.pop(key, None)
                if not task.cancelled() and (error := task.exception()) is not None:
                    logger.warning("Source processing task %s failed (%s)", row.id, type(error).__name__)
            task.add_done_callback(finished)

    async def _extract(self, kb, row, mode):
        store = await self.store(kb)
        started = now()
        state = {"status": "running", "mode": mode, "started_at": started}
        try:
            await store.update_conversation_metadata(row.id, {"management": state})
            if mode == "knowledge":
                ingestion, provenance = self._ingestion_factory(self.spec(kb), store)
                await store.update_conversation_metadata(row.id, {"extraction": provenance})
                result = await ingestion.ingest_stored(row)
                state.update(stored=len(result.records), merged=[{
                    "subject": record.subject, "claim": record.claim, "type": record.type.value,
                    "target": (record.revision.get("consolidation") or {}).get("target"),
                } for record in result.merged])
            else:
                spec = self.spec(kb)
                chunks = chunk_document(Document(
                    id=str(row.id), text=row.transcript,
                    title=row.metadata.get("name", ""), metadata={
                        "evidence_id": str(row.id), "review_status": "draft", "created_at": started,
                        "source_kind": row.metadata.get("source_kind", "text"),
                        "upload_id": row.metadata.get("upload_id"),
                        "transcript_sha256": transcript_sha256(row.transcript),
                    }), spec.chunking)
                documents = await self.documents(kb)
                await documents.ensure_config(index_config(spec.chunking))
                state["stored"] = await documents.add_chunks(chunks)
                await store.mark_conversation(row.id, ConversationStatus.PROCESSED)
            state.update(status="done", finished_at=now())
        except asyncio.CancelledError:
            state.update(status="interrupted", finished_at=now())
            raise
        except Exception as exc:
            logger.warning("Source processing %s failed (%s)", row.id, type(exc).__name__)
            state.update(status="failed", finished_at=now(), error=f"Xử lý thất bại ({type(exc).__name__}). Kiểm tra cấu hình dịch vụ rồi thử lại.")
        finally:
            await store.update_conversation_metadata(row.id, {"management": state})

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
        tasks = list(self._jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for store in [*self._stores.values(), *self._documents.values()]:
            await store.aclose()
