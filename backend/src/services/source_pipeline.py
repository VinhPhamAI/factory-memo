"""Stage handlers for source jobs: raw -> converted -> extracted.

`SourcePipeline.run(job)` performs one queued stage for one source; the job
worker (`backend.worker`) calls it, and the knowledge management API reuses
the same lookups to validate requests before queueing them.

- convert: an uploaded original becomes text. Only plain-text formats have a
  converter (strict decoding); OCR and speech-to-text are not implemented for
  uploads. Recorded transcripts are stored already converted.
- extract: the text becomes evidence (a `raw_conversations` row, created on
  first extraction of an upload), then either knowledge records (LLM
  extraction) or document chunks. Results are drafts.

A handler raises `JobFailed` with a message for the reviewer; `retry` says
whether trying again may help. A stage that already has results (a worker
that died after saving them) completes without running again, so a retried
job never duplicates knowledge or chunks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from backend.api.voice_capture import default_ingestion_factory
from backend.src.agents.rag import Document, DocumentStore, chunk_document
from backend.src.agents.rag.indexing import index_config
from backend.src.services.knowledge_ingestion import transcript_sha256
from backend.src.services.voice_capture import MAX_TEXT_CHARS
from backend.src.storage import KnowledgeStore
from backend.src.storage.document_uploads import DocumentUploadStore, text_convertible
from backend.src.storage.job_queue import Job
from backend.src.storage.models import ConversationStatus


logger = logging.getLogger(__name__)

NO_CONVERTER = "Chưa có OCR/bộ chuyển đổi cho loại file này. File gốc đã được lưu."
UNREADABLE_TEXT = "File không phải văn bản đọc được (UTF-8/UTF-16) hoặc rỗng; không thể chuyển đổi."


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobFailed(Exception):
    """A stage failure with a reviewer-facing message."""

    def __init__(self, message: str, *, retry: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retry = retry


def failure(exc: BaseException) -> tuple[str, bool]:
    """Reviewer-facing message and whether another attempt may succeed.

    Invalid input or output (ValueError, including schema validation) fails at
    once; service and connection errors are retried with backoff.
    """

    if isinstance(exc, JobFailed):
        return exc.message, exc.retry
    if isinstance(exc, (LookupError, FileNotFoundError)):
        return "Không tìm thấy nguồn hoặc knowledge base của job.", False
    retry = not isinstance(exc, (ValueError, UnicodeError, TypeError))
    return f"Xử lý thất bại ({type(exc).__name__}). Kiểm tra cấu hình dịch vụ rồi thử lại.", retry


async def open_documents(spec):
    return await DocumentStore.for_database(
        spec.database, table=spec.document_table,
        text_search_config=spec.text_search_config,
        query_instruction=spec.embedding_query_instruction,
    )


class SourcePipeline:
    def __init__(self, specs, uploads: DocumentUploadStore, *,
                 open_store=KnowledgeStore.for_kb, open_document_store=open_documents,
                 ingestion_factory=default_ingestion_factory):
        self.specs = dict(specs)
        self.uploads = uploads
        self._open_store = open_store
        self._open_documents = open_document_store
        self._ingestion_factory = ingestion_factory
        self._stores: dict[str, Any] = {}
        self._documents: dict[str, Any] = {}
        self._store_lock = asyncio.Lock()

    # ----------------------------------------------------------- lookup

    def add(self, spec):
        """Knowledge bases created in the UI (catalog listener)."""

        self.specs[spec.name] = spec

    def spec(self, kb):
        try:
            return self.specs[kb]
        except KeyError:
            raise LookupError(f"Knowledge base {kb!r} not found") from None

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

    async def has_results(self, kb, row) -> bool:
        if row.status is ConversationStatus.PROCESSED or await (await self.store(kb)).knowledge_for_evidence(row.id):
            return True
        # Chunks are only looked up for document-mode sources (one more database).
        modes = {(row.metadata.get("management") or {}).get("mode"), self.spec(kb).retrieval.value}
        return "documents" in modes and bool(await (await self.documents(kb)).get_document(str(row.id)))

    async def upload_evidence(self, kb, upload_id):
        """The evidence row of a converted upload; created on first use."""

        store = await self.store(kb)
        upload = await asyncio.to_thread(self.uploads.get, kb, upload_id)
        state = await asyncio.to_thread(self.uploads.processing, kb, upload_id)
        if state.get("evidence_id"):
            row = await store.get_conversation(UUID(state["evidence_id"]))
            if row is not None:
                return row
        text = await asyncio.to_thread(self.uploads.converted_text, kb, upload_id)
        if text is None:
            raise JobFailed("File chưa được chuyển đổi sang văn bản.")
        row = await store.add_conversation(text, metadata={
            "source_kind": "document_upload", "upload_id": upload.id,
            "name": upload.filename, "filename": upload.filename,
            "original_sha256": upload.sha256, "storage_key": upload.storage_key,
            "transcript_sha256": transcript_sha256(text),
        })
        state["evidence_id"] = str(row.id)
        await asyncio.to_thread(self.uploads.update_processing, kb, upload_id, state)
        return row

    # ----------------------------------------------------------- stages

    async def run(self, job: Job) -> dict[str, Any]:
        handler = {"convert": self.run_convert, "extract": self.run_extract}.get(job.stage)
        if handler is None:
            raise JobFailed(f"Không có worker cho bước {job.stage!r}.")
        return await handler(job)

    async def run_convert(self, job: Job) -> dict[str, Any]:
        if job.source_kind != "upload":
            raise JobFailed("Chỉ file tải lên cần bước chuyển đổi.")
        self.spec(job.kb)
        upload = await asyncio.to_thread(self.uploads.get, job.kb, job.source_id)
        if not text_convertible(upload):
            raise JobFailed(NO_CONVERTER)
        try:
            state = await asyncio.to_thread(self.uploads.convert_text, job.kb, job.source_id)
        except (ValueError, UnicodeError) as exc:
            raise JobFailed(UNREADABLE_TEXT) from exc
        conversion = state["conversion"]
        return {"converter": "text", "encoding": conversion.get("encoding"), "sha256": conversion.get("sha256")}

    async def run_extract(self, job: Job) -> dict[str, Any]:
        spec = self.spec(job.kb)
        mode = job.mode or spec.retrieval.value
        store = await self.store(job.kb)
        if job.source_kind == "upload":
            row = await self.upload_evidence(job.kb, job.source_id)
        else:
            row = await store.get_conversation(UUID(job.source_id))
            if row is None:
                raise JobFailed("Không tìm thấy transcript của job.")
        if await self.has_results(job.kb, row):
            # An earlier attempt saved its results before its worker stopped.
            await store.update_conversation_metadata(row.id, {"management": {
                **(row.metadata.get("management") or {}), "status": "done", "mode": mode,
                "job_id": str(job.id), "finished_at": now(),
            }})
            return {"evidence_id": str(row.id), "skipped": "already_extracted"}
        if mode == "knowledge" and len(row.transcript) > MAX_TEXT_CHARS:
            raise JobFailed(f"Trích xuất tri thức nhận tối đa {MAX_TEXT_CHARS} ký tự; hãy chọn chế độ document chunks.")

        state: dict[str, Any] = {"status": "running", "mode": mode, "job_id": str(job.id),
                                 "attempt": job.attempts, "started_at": now()}
        await store.update_conversation_metadata(row.id, {"management": state})
        try:
            if mode == "knowledge":
                ingestion, provenance = self._ingestion_factory(spec, store)
                await store.update_conversation_metadata(row.id, {"extraction": provenance})
                result = await ingestion.ingest_stored(row)
                state.update(stored=len(result.records), merged=[{
                    "subject": record.subject, "claim": record.claim, "type": record.type.value,
                    "target": (record.revision.get("consolidation") or {}).get("target"),
                } for record in result.merged])
            else:
                chunks = chunk_document(Document(
                    id=str(row.id), text=row.transcript,
                    title=row.metadata.get("name", ""), metadata={
                        "evidence_id": str(row.id), "review_status": "draft", "created_at": state["started_at"],
                        "source_kind": row.metadata.get("source_kind", "text"),
                        "upload_id": row.metadata.get("upload_id"),
                        "transcript_sha256": transcript_sha256(row.transcript),
                    }), spec.chunking)
                documents = await self.documents(job.kb)
                await documents.ensure_config(index_config(spec.chunking))
                state["stored"] = await documents.add_chunks(chunks)
                await store.mark_conversation(row.id, ConversationStatus.PROCESSED)
        except asyncio.CancelledError:
            # Shutdown: the job goes back to the queue and runs again.
            state.update(status="interrupted", finished_at=now())
            await asyncio.shield(store.update_conversation_metadata(row.id, {"management": state}))
            raise
        except Exception as exc:
            logger.warning("Extraction job %s for %s failed (%s)", job.id, row.id, type(exc).__name__)
            message, _ = failure(exc)
            state.update(status="failed", finished_at=now(), error=message)
            await store.update_conversation_metadata(row.id, {"management": state})
            raise
        state.update(status="done", finished_at=now())
        await store.update_conversation_metadata(row.id, {"management": state})
        return {"evidence_id": str(row.id), "mode": mode, "stored": state.get("stored", 0)}

    async def aclose(self):
        for store in [*self._stores.values(), *self._documents.values()]:
            await store.aclose()
        self._stores.clear()
        self._documents.clear()
