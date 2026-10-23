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
- conflict: the knowledge records of the evidence are compared with stored
  records of a limited candidate set (the KB's `ConflictDetector`); verdicts go
  to `knowledge_conflicts`, and the summary to the evidence metadata
  (`conflicts`). Queued after extract only when the KB enables the stage.

A handler raises `JobFailed` with a message for the reviewer; `retry` says
whether trying again may help. A stage that already has results (a worker
that died after saving them) completes without running again, so a retried
job never duplicates knowledge or chunks.

Each extraction is recorded as an extraction run (`extraction_runs`). Content
that another evidence row already extracted (or is extracting) in the same
mode is not extracted again: the job completes with the source marked
`duplicate` and a link to the earlier source, without an LLM call.

Content that nearly matches an extracted source in the same mode and KB (5-word
shingle Jaccard or containment above the thresholds, `content_signatures.py`)
is not extracted until the reviewer confirms: the job completes with the source
marked `near_duplicate` and the matches, and a job queued with the option
`confirm_overlap` extracts it. Smaller overlaps are recorded for information.
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
from backend.src.storage.content_signatures import (
    CONFIRM_LEVELS, SIGNATURE_VERSION, Thresholds, overlap, shingles, signature,
)
from backend.src.storage.document_uploads import DocumentUploadStore, text_convertible
from backend.src.storage.extraction_runs import (
    LEGACY_FINGERPRINT, ExtractionRun, ExtractionRunStore, RunConflict,
    content_sha256, extraction_fingerprint,
)
from backend.src.storage.job_queue import Job
from backend.src.storage.knowledge_conflicts import ConflictStore
from backend.src.storage.models import ConversationStatus


logger = logging.getLogger(__name__)

OVERLAP_CANDIDATES = 20
MAX_OVERLAPS = 5
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


async def open_runs(spec, store):
    runs = ExtractionRunStore(store.engine, schema=store.schema)
    await runs.init_schema()
    return runs


async def open_conflicts(spec, store):
    conflicts = ConflictStore(store.engine, schema=store.schema)
    await conflicts.init_schema()
    return conflicts


def default_conflict_factory(spec):
    """The KB's conflict detector, or None when it has no conflict stage."""

    return spec.conflict_factory(spec) if getattr(spec, "conflict_factory", None) else None


def duplicate_message(duplicate: dict[str, Any]) -> str:
    name = duplicate.get("name") or duplicate.get("evidence_id")
    if duplicate.get("status") == "running":
        return f"Nội dung trùng với «{name}» đang được trích xuất; không trích xuất lặp."
    if duplicate.get("same_logic") is False:
        return (f"Nội dung trùng với «{name}» đã trích xuất bằng logic trích xuất cũ. "
                "Trích xuất lại có xác nhận chưa được hỗ trợ.")
    return f"Nội dung trùng với «{name}» đã được trích xuất; không trích xuất lặp."


def overlap_message(matches: list[dict[str, Any]]) -> str:
    match = matches[0]
    name = match.get("name") or match.get("evidence_id")
    if match["level"] == "near_duplicate":
        found = f"gần như trùng với «{name}» (Jaccard {match['jaccard']:.0%})"
    else:
        found = f"là một phần của «{name}» ({match['containment']:.0%} nội dung nằm trong nguồn đó)"
    more = f" và {len(matches) - 1} nguồn khác" if len(matches) > 1 else ""
    return (f"Nội dung {found}{more}, nguồn đã được trích xuất. "
            "Xác nhận nếu vẫn muốn trích xuất.")


class SourcePipeline:
    def __init__(self, specs, uploads: DocumentUploadStore, *,
                 open_store=KnowledgeStore.for_kb, open_document_store=open_documents,
                 ingestion_factory=default_ingestion_factory, open_runs=open_runs,
                 conflict_factory=default_conflict_factory, open_conflicts=open_conflicts,
                 thresholds: Thresholds | None = None):
        self.specs = dict(specs)
        self.thresholds = thresholds or Thresholds.from_env()
        self.uploads = uploads
        self._open_store = open_store
        self._open_documents = open_document_store
        self._open_runs = open_runs
        self._ingestion_factory = ingestion_factory
        self._conflict_factory = conflict_factory
        self._open_conflicts = open_conflicts
        self._stores: dict[str, Any] = {}
        self._documents: dict[str, Any] = {}
        self._runs: dict[str, Any] = {}
        self._conflicts: dict[str, Any] = {}
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

    async def runs(self, kb):
        store = await self.store(kb)
        async with self._store_lock:
            if kb not in self._runs:
                self._runs[kb] = await self._open_runs(self.spec(kb), store)
        return self._runs[kb]

    async def conflicts(self, kb):
        store = await self.store(kb)
        async with self._store_lock:
            if kb not in self._conflicts:
                self._conflicts[kb] = await self._open_conflicts(self.spec(kb), store)
        return self._conflicts[kb]

    def conflict_stages(self, kb, mode) -> tuple[str, ...]:
        """`("conflict",)` when extracting knowledge in a KB with a conflict stage."""

        if mode != "knowledge":
            return ()
        try:
            return ("conflict",) if self._conflict_factory(self.spec(kb)) is not None else ()
        except Exception:
            # A broken conflict configuration must not block extraction.
            logger.warning("Could not load the conflict detector of %s", kb, exc_info=True)
            return ()

    def extraction_settings(self, spec, store, mode):
        """(settings, ingestion service or None) of the current extraction logic."""

        if mode == "knowledge":
            ingestion, provenance = self._ingestion_factory(spec, store)
            return provenance, ingestion
        return index_config(spec.chunking), None

    def current_fingerprint(self, spec, store, mode) -> str | None:
        try:
            return extraction_fingerprint(mode, self.extraction_settings(spec, store, mode)[0])
        except Exception:
            # Only labels a duplicate as same or older logic; unknown is acceptable.
            logger.warning("Could not build the %s extraction settings of %s", mode, spec.name)
            return None

    async def backfill(self, kb) -> int:
        """Records extractions made before runs existed, or by the legacy capture path."""

        spec, runs = self.spec(kb), await self.runs(kb)
        rows = await runs.unrecorded()
        for row in rows:
            metadata = row.metadata
            mode = (metadata.get("management") or {}).get("mode") or (
                "knowledge" if row.has_knowledge or metadata.get("extraction") else spec.retrieval.value)
            settings = metadata.get("extraction") if mode == "knowledge" else None
            await runs.record_legacy(
                evidence_id=row.id, content=content_sha256(row.transcript), mode=mode,
                fingerprint=extraction_fingerprint(mode, settings) if settings else LEGACY_FINGERPRINT,
                settings=settings or {}, original_sha256=metadata.get("original_sha256"),
            )
        return len(rows)

    @staticmethod
    def source_info(run: ExtractionRun, row) -> dict[str, Any]:
        """The earlier extracted source of a run, for links shown to the reviewer."""

        metadata = row.metadata if row is not None else {}
        upload_id = metadata.get("upload_id")
        return {
            "run_id": str(run.id), "evidence_id": str(run.evidence_id), "status": run.status,
            "mode": run.mode, "name": metadata.get("name") or metadata.get("filename") or str(run.evidence_id),
            "kind": "upload" if upload_id else "conversation",
            "source_id": upload_id or str(run.evidence_id),
            "extracted_at": (run.finished_at or run.created_at).isoformat(),
        }

    async def duplicate_info(self, kb, run: ExtractionRun, fingerprint: str | None) -> dict[str, Any]:
        """The earlier source that already covers this content, for the reviewer."""

        row = await (await self.store(kb)).get_conversation(run.evidence_id)
        return {**self.source_info(run, row),
                "same_logic": None if fingerprint is None else run.fingerprint == fingerprint}

    async def sign(self, kb, evidence_id, transcript) -> None:
        signed = await asyncio.to_thread(signature, transcript)
        await (await self.runs(kb)).put_signature(
            evidence_id, content=content_sha256(transcript), version=SIGNATURE_VERSION,
            shingle_count=len(signed.shingles), bands=signed.bands)

    async def sign_extracted(self, kb) -> int:
        """Signs extracted rows that have no signature yet (older rows, or a failed write)."""

        runs, store, signed = await self.runs(kb), await self.store(kb), 0
        while ids := await runs.unsigned(SIGNATURE_VERSION):
            for evidence_id in ids:
                row = await store.get_conversation(evidence_id)
                await self.sign(kb, evidence_id, row.transcript if row is not None else "")
                signed += 1
        return signed

    async def overlaps(self, kb, mode, text, *, exclude=None) -> list[dict[str, Any]]:
        """Extracted sources in this KB and mode whose text overlaps `text`, strongest first.

        Each match has its `level` (`near_duplicate` and `contained` need the
        reviewer's confirmation; `similar` informs) and its exact scores.
        """

        await self.backfill(kb)
        await self.sign_extracted(kb)
        runs, store = await self.runs(kb), await self.store(kb)
        new = await asyncio.to_thread(signature, text)
        candidates = await runs.overlap_candidates(
            new.bands, mode, version=SIGNATURE_VERSION, content=content_sha256(text),
            exclude=exclude, limit=OVERLAP_CANDIDATES)
        matches = []
        for run, _ in candidates:
            row = await store.get_conversation(run.evidence_id)
            if row is None:
                continue
            scores = overlap(new.shingles, await asyncio.to_thread(shingles, row.transcript))
            level = self.thresholds.level(scores)
            if level:
                matches.append({**self.source_info(run, row), "level": level,
                                "jaccard": round(scores.jaccard, 3), "containment": round(scores.containment, 3),
                                "coverage": round(scores.coverage, 3)})
        matches.sort(key=lambda match: (match["level"] not in CONFIRM_LEVELS,
                                        -max(match["jaccard"], match["containment"], match["coverage"])))
        return matches[:MAX_OVERLAPS]

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
        handler = {"convert": self.run_convert, "extract": self.run_extract,
                   "conflict": self.run_conflict}.get(job.stage)
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
        runs = await self.runs(job.kb)
        if await self.has_results(job.kb, row):
            # An earlier attempt saved its results before its worker stopped.
            for run in await runs.for_evidence(row.id):
                if run.status == "running":
                    await runs.finish(run.id, "done", result={"recovered": True})
            await store.update_conversation_metadata(row.id, {"management": {
                **(row.metadata.get("management") or {}), "status": "done", "mode": mode,
                "job_id": str(job.id), "finished_at": now(),
            }})
            return {"evidence_id": str(row.id), "skipped": "already_extracted"}
        if mode == "knowledge" and len(row.transcript) > MAX_TEXT_CHARS:
            raise JobFailed(f"Trích xuất tri thức nhận tối đa {MAX_TEXT_CHARS} ký tự; hãy chọn chế độ document chunks.")

        content = content_sha256(row.transcript)
        state: dict[str, Any] = {"status": "running", "mode": mode, "job_id": str(job.id),
                                 "attempt": job.attempts, "started_at": now()}
        run = None

        async def duplicate(earlier: ExtractionRun) -> dict[str, Any]:
            # Same content already extracted from another source: no LLM call.
            info = await self.duplicate_info(job.kb, earlier, fingerprint)
            state.update(status="duplicate", finished_at=now(), duplicate_of=info)
            await store.update_conversation_metadata(row.id, {"management": state, "content_sha256": content})
            return {"evidence_id": str(row.id), "mode": mode, "skipped": "duplicate_content",
                    "duplicate_of": info}

        try:
            settings, ingestion = self.extraction_settings(spec, store, mode)
            fingerprint = extraction_fingerprint(mode, settings)
            await self.backfill(job.kb)
            if earlier := await runs.find_active(content, mode, exclude=row.id):
                return await duplicate(earlier)
            overlaps = await self.overlaps(job.kb, mode, row.transcript, exclude=row.id)
            confirm = [match for match in overlaps if match["level"] in CONFIRM_LEVELS]
            if confirm and not job.options.get("confirm_overlap"):
                # Nearly the same as an extracted source: wait for the reviewer.
                state.update(status="near_duplicate", finished_at=now(), overlaps=overlaps)
                await store.update_conversation_metadata(row.id, {"management": state, "content_sha256": content})
                return {"evidence_id": str(row.id), "mode": mode, "skipped": "near_duplicate",
                        "overlaps": overlaps}
            if overlaps:
                state["overlaps"] = overlaps
            if confirm:
                state["overlap_confirmed"] = True
            try:
                run = await runs.start(
                    evidence_id=row.id, content=content, mode=mode, fingerprint=fingerprint,
                    settings=settings, original_sha256=row.metadata.get("original_sha256"),
                    job_id=str(job.id),
                )
            except RunConflict as exc:
                return await duplicate(exc.run)
            try:
                await self.sign(job.kb, row.id, row.transcript)
            except Exception:
                # The next overlap check signs rows it finds unsigned.
                logger.warning("Could not store the signature of %s", row.id, exc_info=True)
            await store.update_conversation_metadata(row.id, {
                "management": state, "content_sha256": content,
                **({"extraction": settings} if mode == "knowledge" else {}),
            })
            if mode == "knowledge":
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
            # Shutdown: the job goes back to the queue and runs again; its rerun
            # takes over the still-running run of this evidence row.
            state.update(status="interrupted", finished_at=now())
            await asyncio.shield(store.update_conversation_metadata(row.id, {"management": state}))
            raise
        except Exception as exc:
            logger.warning("Extraction job %s for %s failed (%s)", job.id, row.id, type(exc).__name__)
            message, _ = failure(exc)
            state.update(status="failed", finished_at=now(), error=message)
            await store.update_conversation_metadata(row.id, {"management": state})
            if run is not None:
                await runs.finish(run.id, "failed", error=message)
            raise
        state.update(status="done", finished_at=now())
        await runs.finish(run.id, "done", result={"stored": state.get("stored", 0), "job_id": str(job.id)})
        await store.update_conversation_metadata(row.id, {"management": state})
        return {"evidence_id": str(row.id), "mode": mode, "stored": state.get("stored", 0)}

    async def run_conflict(self, job: Job) -> dict[str, Any]:
        """Judges the new records of one evidence row against stored records.

        Retrying is safe: judged pairs are cached in `knowledge_conflicts` and
        links are added only when missing. Records stay drafts.
        """

        spec = self.spec(job.kb)
        detector = self._conflict_factory(spec)
        if detector is None:
            return {"skipped": "conflict detection disabled"}
        store = await self.store(job.kb)
        if job.source_kind == "upload":
            state = await asyncio.to_thread(self.uploads.processing, job.kb, job.source_id)
            evidence_id = state.get("evidence_id")
        else:
            evidence_id = job.source_id
        row = await store.get_conversation(UUID(evidence_id)) if evidence_id else None
        if row is None:
            return {"skipped": "no evidence"}
        result = await detector.adetect(store, await self.conflicts(job.kb), row.id, job_id=str(job.id))
        if "skipped" not in result:
            counts = ("pairs", "judged", "cached", "rechecked", "closed", "truncated", "no_conflict", "open",
                      "pending", "needs_review", "linked", "reviewed", "llm_calls")
            await store.update_conversation_metadata(row.id, {"conflicts": {
                "status": "done", "job_id": str(job.id), "finished_at": now(),
                "judge_mode": result["judge_mode"], "judge_fingerprint": result["judge_fingerprint"],
                **{key: result[key] for key in counts if key in result},
            }})
        return result

    async def aclose(self):
        # Run and conflict stores share their knowledge store's engine.
        for store in [*self._stores.values(), *self._documents.values()]:
            await store.aclose()
        self._stores.clear()
        self._documents.clear()
        self._runs.clear()
        self._conflicts.clear()
