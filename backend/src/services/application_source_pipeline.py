"""Run application-database conversion, extraction and conflict stages.

The legacy pipeline remains available to benchmarks and command-line workflows.
This pipeline is the serving path: every source, job, extraction run, chunk,
signature and conflict belongs to the single database configured by
``APP_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.voice_capture import default_ingestion_factory
from backend.src.agents.rag import Document, chunk_document
from backend.src.agents.rag.indexing import index_config
from backend.src.services.converters import ConverterError, Segment, convert_file
from backend.src.services.demo.knowledge_extraction import load_demo_knowledge_extraction
from backend.src.services.knowledge_conflicts import link_updates, stale
from backend.src.services.knowledge_ingestion import transcript_sha256
from backend.src.services.located_evidence import (
    references_for_chunks,
    references_for_record,
    segments_from_metadata,
)
from backend.src.services.source_pipeline import (
    MAX_OVERLAPS,
    OVERLAP_CANDIDATES,
    NO_CONVERTER,
    UNREADABLE_TEXT,
    JobFailed,
    failure,
    now,
)
from backend.src.services.voice_capture import MAX_TEXT_CHARS
from backend.src.storage.content_signatures import (
    CONFIRM_LEVELS,
    SIGNATURE_VERSION,
    Thresholds,
    overlap,
    shingles,
    signature,
)
from backend.src.storage.embeddings import (
    EmbeddingMismatch,
    build_embeddings_from_env,
    check_embedding_model,
    embedding_dim_from_env,
    embedding_model_from_env,
)
from backend.src.storage.extraction_runs import content_sha256, extraction_fingerprint
from backend.src.storage.knowledge_conflicts import ConflictRow
from backend.src.storage.models import (
    ConversationStatus,
    KnowledgeLinkUpdate,
    KnowledgeRecord,
    KnowledgeStatus,
    RawConversation,
)
from backend.src.storage.repositories.chunks import ChunkRepository
from backend.src.storage.repositories.content_signatures import ContentSignatureRepository
from backend.src.storage.repositories.extraction_runs import (
    ExtractionRun,
    ExtractionRunRepository,
    RunConflict,
)
from backend.src.storage.repositories.ingestion_jobs import IngestionJobRepository, Job, PreparedJobResult
from backend.src.storage.repositories.knowledge_conflicts import ConflictRepository
from backend.src.storage.repositories.sources import FileEntry, SourceEntry, SourceRepository


logger = logging.getLogger(__name__)


def _text_convertible(file: FileEntry) -> bool:
    """Return whether the existing plain-text converter supports a file."""

    return file.content_type.startswith("text/") or Path(file.filename).suffix.lower() in {
        ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log",
    }


def convert_plain_text(file: FileEntry, path: Path) -> tuple[str, dict[str, str]]:
    """Decode one supported file strictly and return its text and audit metadata.

    Args:
        file: Database metadata of the original bytes.
        path: Validated location of those bytes in the file store.

    Returns:
        Decoded text and conversion metadata containing encoding, digest and time.

    Raises:
        JobFailed: The file type is unsupported or its bytes are not readable text.
    """

    if not _text_convertible(file):
        raise JobFailed(NO_CONVERTER)
    try:
        raw = path.read_bytes()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = raw.decode(encoding)
    except (ValueError, UnicodeError) as exc:
        raise JobFailed(UNREADABLE_TEXT) from exc
    if not text.strip() or "\x00" in text:
        raise JobFailed(UNREADABLE_TEXT)
    return text, {
        "encoding": encoding,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "at": now(),
    }


class ApplicationKnowledgeStore:
    """Expose ingestion and conflict store operations through chunk repositories."""

    def __init__(
        self,
        knowledge_base_id: UUID,
        source_id: UUID,
        chunks: ChunkRepository,
        embeddings: Any,
        *,
        vector_size: int,
        leading_keys: tuple[str, ...] = (),
        source_text: str = "",
        segments: tuple[Segment, ...] = (),
        defer_writes: bool = False,
    ) -> None:
        self.knowledge_base_id = knowledge_base_id
        self.source_id = source_id
        self.chunks = chunks
        self.embeddings = embeddings
        self.vector_size = vector_size
        self.leading_keys = leading_keys
        self.source_text = source_text
        self.segments = segments
        self.defer_writes = defer_writes
        self.write_chunks: Callable[..., Any] | None = None

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed texts and reject vectors that do not match the database dimension."""

        if not texts:
            return []
        vectors = [list(vector) for vector in await self.embeddings.aembed_documents(texts)]
        for vector in vectors:
            if len(vector) != self.vector_size:
                raise ValueError(
                    f"Embedding has {len(vector)} dimensions, expected {self.vector_size}"
                )
        return vectors

    async def embed_knowledge(self, records: list[KnowledgeRecord]) -> None:
        """Fill missing record embeddings in one provider request."""

        missing = [record for record in records if record.embedding is None]
        vectors = await self.embed_texts(
            [record.embedding_text(self.leading_keys) for record in missing]
        )
        for record, vector in zip(missing, vectors, strict=True):
            record.embedding = vector

    async def add_knowledge(
        self,
        records: list[KnowledgeRecord],
        *,
        updates: list[KnowledgeLinkUpdate] | tuple[KnowledgeLinkUpdate, ...] = (),
    ) -> list[UUID]:
        """Embed records and either store them or defer writes to job completion."""

        await self.embed_knowledge(records)
        references_by_record = {
            record.id: references_for_record(
                record, self.source_text, self.segments
            )
            for record in records
        }
        write = partial(
            self.chunks.add_records_and_updates,
            self.knowledge_base_id,
            self.source_id,
            records,
            updates=updates,
            leading_keys=self.leading_keys,
            references_by_record=references_by_record,
        )
        if not self.defer_writes:
            return await write()
        self.write_chunks = write
        return [record.id for record in records]

    async def mark_conversation(
        self,
        _conversation_id: UUID,
        _status: ConversationStatus,
        *,
        error: str | None = None,
    ) -> None:
        """Leave source status transitions to the job queue transaction."""

    async def find_processed_conversation(
        self, _digest: str, *, without_key: str | None = None
    ) -> UUID | None:
        """Return no legacy duplicate; extraction runs own duplicate blocking."""

        return None

    async def knowledge_for_evidence(
        self, source_id: UUID, *, embeddings: bool = False
    ) -> list[KnowledgeRecord]:
        """Return knowledge records linked to one source."""

        return await self.chunks.records_for_source(
            self.knowledge_base_id, source_id, embeddings=embeddings
        )

    async def get_knowledge(self, ids: list[UUID]) -> list[KnowledgeRecord]:
        """Return knowledge records by ID for consolidation and conflict review."""

        return await self.chunks.get_records(self.knowledge_base_id, ids)

    async def similar_knowledge(self, record: KnowledgeRecord, **options: Any) -> Any:
        """Find vector candidates for consolidation or conflict detection."""

        if record.embedding is None:
            await self.embed_knowledge([record])
        return await self.chunks.similar_records(
            self.knowledge_base_id, record, **options
        )

    async def lexical_knowledge(self, query: str, **options: Any) -> Any:
        """Find keyword candidates for conflict detection."""

        return await self.chunks.lexical_records(
            self.knowledge_base_id, query, **options
        )


class ApplicationSourcePipeline:
    """Process application knowledge sources without opening legacy databases."""

    def __init__(
        self,
        catalog: Any,
        sources: SourceRepository,
        chunks: ChunkRepository,
        uploads: Any,
        sessions: Any,
        *,
        ingestion_factory: Callable[[Any, Any], tuple[Any, dict[str, Any]]] = default_ingestion_factory,
        embeddings_factory: Callable[..., Any] = build_embeddings_from_env,
        vector_size: int | None = None,
        thresholds: Thresholds | None = None,
    ) -> None:
        self.catalog = catalog
        self.sources = sources
        self.chunks = chunks
        self.uploads = uploads
        self.sessions = sessions
        self._ingestion_factory = ingestion_factory
        self._embeddings_factory = embeddings_factory
        self._vector_size = vector_size
        self.thresholds = thresholds or Thresholds.from_env()
        self._embeddings: dict[str, Any] = {}

    def add(self, _spec: Any) -> None:
        """Accept catalog listener notifications; policies are resolved per job."""

    async def _resolve(self, knowledge_base_id: UUID) -> tuple[str, Any]:
        """Resolve the current policy even if the KB was previously cached."""

        await self.catalog.refresh()
        resolved = self.catalog.resolve_id(knowledge_base_id)
        if resolved is None:
            raise LookupError(f"Knowledge base {knowledge_base_id} not found")
        return resolved

    def _store(
        self,
        knowledge_base_id: UUID,
        source: SourceEntry,
        spec: Any,
        *,
        defer_writes: bool = False,
    ) -> ApplicationKnowledgeStore:
        """Build the ingestion adapter with the source's located conversion spans."""

        embeddings = self._embeddings.get(spec.name)
        if embeddings is None:
            embeddings = self._embeddings_factory(
                query_instruction=spec.embedding_query_instruction
            )
            self._embeddings[spec.name] = embeddings
        if self._vector_size is None:
            self._vector_size = embedding_dim_from_env()
        return ApplicationKnowledgeStore(
            knowledge_base_id,
            source.id,
            self.chunks,
            embeddings,
            vector_size=self._vector_size,
            leading_keys=spec.embedding_context_keys,
            source_text=source.extracted_text or "",
            segments=tuple(segments_from_metadata(source.metadata)),
            defer_writes=defer_writes,
        )

    async def _source(self, job: Job) -> tuple[str, Any, SourceEntry]:
        kb_name, spec = await self._resolve(job.knowledge_base_id)
        source = await self.sources.get(job.knowledge_base_id, job.source_id)
        if source is None:
            raise JobFailed("Không tìm thấy nguồn của job.")
        return kb_name, spec, source

    async def run(self, job: Job) -> dict[str, Any] | PreparedJobResult:
        """Dispatch one claimed stage job."""

        handler = {
            "convert": self.run_convert,
            "extract": self.run_extract,
            "conflict": self.run_conflict,
        }.get(job.stage)
        if handler is None:
            raise JobFailed(f"Không có worker cho bước {job.stage!r}.")
        return await handler(job)

    async def _sign(self, knowledge_base_id: UUID, source_id: UUID, text: str) -> None:
        signed = await asyncio.to_thread(signature, text)
        await ContentSignatureRepository(self.sessions, knowledge_base_id).put(
            source_id,
            content=content_sha256(text),
            version=SIGNATURE_VERSION,
            shingle_count=len(signed.shingles),
            bands=signed.bands,
        )

    async def run_convert(self, job: Job) -> dict[str, Any]:
        """Convert original bytes and store text plus conversion audit metadata."""

        _kb_name, _spec, source = await self._source(job)
        if source.file is None:
            raise JobFailed("Chỉ file tải lên cần bước chuyển đổi.")
        demo = load_demo_knowledge_extraction(source.title, source.file.content_type)
        if source.extracted_text is None:
            if demo is not None:
                await asyncio.sleep(demo.timing.conversion_seconds)
                text = demo.text
                conversion: dict[str, Any] = {
                    "converter": "demo_artifact",
                    "version": 1,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "at": now(),
                    "details": {
                        "demo": True,
                        "configured_title": demo.title,
                    },
                    "segments": [],
                }
            else:
                try:
                    converted = await convert_file(
                        self.uploads.content_path(source.file.storage_key),
                        source.file.content_type,
                        source.file.filename,
                    )
                except ConverterError as exc:
                    raise JobFailed(exc.message, retry=exc.retry) from exc
                text = converted.text
                conversion = {
                    "converter": converted.converter,
                    "version": 1,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "at": now(),
                    "details": converted.details,
                    "segments": [segment.to_json() for segment in converted.segments],
                }
            source = await self.sources.update(
                job.knowledge_base_id,
                job.source_id,
                extracted_text=text,
                metadata={"conversion": conversion},
            )
            if source is None:
                raise JobFailed("Không tìm thấy nguồn của job.")
        else:
            conversion = dict(source.metadata.get("conversion") or {})
            if not conversion:
                conversion = {
                    "converter": "existing_text",
                    "version": 1,
                    "sha256": hashlib.sha256(source.extracted_text.encode()).hexdigest(),
                    "at": now(),
                    "details": {},
                    "segments": [],
                }
                await self.sources.update(
                    job.knowledge_base_id,
                    job.source_id,
                    metadata={"conversion": conversion},
                )
        await self._sign(job.knowledge_base_id, job.source_id, source.extracted_text)
        return {
            "converter": conversion.get("converter"),
            "sha256": conversion.get("sha256"),
        }

    @staticmethod
    def _source_info(run: ExtractionRun, source: SourceEntry | None) -> dict[str, Any]:
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

    async def _duplicate_info(
        self, run: ExtractionRun, fingerprint: str | None
    ) -> dict[str, Any]:
        source = await self.sources.get(run.knowledge_base_id, run.source_id)
        return {
            **self._source_info(run, source),
            "same_logic": None if fingerprint is None else run.fingerprint == fingerprint,
        }

    async def _overlaps(
        self,
        knowledge_base_id: UUID,
        mode: str,
        text: str,
        *,
        exclude: UUID,
    ) -> list[dict[str, Any]]:
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
        matches: list[dict[str, Any]] = []
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

    async def run_extract(self, job: Job) -> dict[str, Any] | PreparedJobResult:
        """Extract knowledge or passages once, with duplicate and overlap guards."""

        _kb_name, spec, source = await self._source(job)
        mode = job.mode or spec.retrieval.value
        if source.extracted_text is None:
            raise JobFailed("File chưa được chuyển đổi sang văn bản.")
        text = source.extracted_text
        demo = (
            load_demo_knowledge_extraction(source.title, source.file.content_type)
            if source.file is not None and mode == "knowledge"
            else None
        )
        if await self.chunks.has_results(job.knowledge_base_id, source.id, mode):
            runs = ExtractionRunRepository(self.sessions, job.knowledge_base_id)
            for run in await runs.for_source(source.id):
                if run.status == "running":
                    await runs.finish(run.id, "done", result={"recovered": True})
            await self.sources.update(
                job.knowledge_base_id,
                source.id,
                metadata={"management": {
                    "status": "done", "mode": mode, "job_id": str(job.id),
                    "finished_at": now(),
                }},
            )
            return {"evidence_id": str(source.id), "mode": mode, "skipped": "already_extracted"}
        if mode == "knowledge" and len(text) > MAX_TEXT_CHARS:
            raise JobFailed(
                f"Trích xuất tri thức nhận tối đa {MAX_TEXT_CHARS} ký tự; "
                "hãy chọn chế độ document chunks."
            )

        digest = content_sha256(text)
        fingerprint: str | None = None
        state: dict[str, Any] = {
            "status": "running", "mode": mode, "job_id": str(job.id),
            "attempt": job.attempts, "started_at": now(),
        }
        runs = ExtractionRunRepository(self.sessions, job.knowledge_base_id)

        async def duplicate(earlier: ExtractionRun) -> dict[str, Any]:
            info = await self._duplicate_info(earlier, fingerprint)
            state.update(status="duplicate", finished_at=now(), duplicate_of=info)
            await self.sources.update(
                job.knowledge_base_id,
                source.id,
                metadata={"management": state, "content_sha256": digest},
            )
            return {
                "evidence_id": str(source.id), "mode": mode,
                "skipped": "duplicate_content", "duplicate_of": info,
                "_terminal": True,
            }

        original = source.file.sha256 if source.file is not None else source.metadata.get("original_sha256")
        if original and (earlier := await runs.find_active_original(original, mode)) is not None:
            if earlier.source_id != source.id:
                return await duplicate(earlier)
        if earlier := await runs.find_active(digest, mode, exclude=source.id):
            return await duplicate(earlier)

        await self._sign(job.knowledge_base_id, source.id, text)
        overlaps = await self._overlaps(
            job.knowledge_base_id, mode, text, exclude=source.id
        )
        confirmation = [match for match in overlaps if match["level"] in CONFIRM_LEVELS]
        if confirmation and not job.options.get("confirm_overlap"):
            state.update(status="near_duplicate", finished_at=now(), overlaps=overlaps)
            await self.sources.update(
                job.knowledge_base_id,
                source.id,
                metadata={"management": state, "content_sha256": digest},
            )
            return {
                "evidence_id": str(source.id), "mode": mode,
                "skipped": "near_duplicate", "overlaps": overlaps,
                "_terminal": True,
            }
        if overlaps:
            state["overlaps"] = overlaps
        if confirmation:
            state["overlap_confirmed"] = True

        store = None
        if demo is not None:
            settings = {
                "version": 1,
                "demo_artifact": demo.title,
                "artifact_sha256": hashlib.sha256(demo.text.encode()).hexdigest(),
                "records": len(demo.records),
            }
        else:
            store = self._store(job.knowledge_base_id, source, spec, defer_writes=True)
        if mode == "knowledge" and demo is None:
            ingestion, settings = self._ingestion_factory(spec, store)
        elif mode != "knowledge":
            assert store is not None
            ingestion, settings = None, index_config(spec.chunking)
        embedding_model = embedding_model_from_env()
        # Recorded so a later model change is detected before searching or ingesting.
        settings = {
            **settings,
            "embedding_model": embedding_model,
            "embedding_dim": embedding_dim_from_env(),
        }
        fingerprint = extraction_fingerprint(mode, settings)
        assert fingerprint is not None
        try:
            check_embedding_model(
                spec.name, configured=embedding_model, recorded=await runs.embedding_models()
            )
        except EmbeddingMismatch as exc:
            state.update(status="failed", finished_at=now(), error=str(exc))
            await self.sources.update(
                job.knowledge_base_id, source.id, metadata={"management": state}
            )
            raise JobFailed(str(exc)) from exc

        run: ExtractionRun | None = None
        write_chunks: Callable[..., Any] | None = None
        try:
            try:
                run = await runs.start(
                    source_id=source.id,
                    content=digest,
                    mode=mode,
                    fingerprint=fingerprint,
                    settings=settings,
                    original_sha256=original,
                    job_id=job.id,
                )
            except RunConflict as exc:
                return await duplicate(exc.run)
            await self.sources.update(
                job.knowledge_base_id,
                source.id,
                metadata={"management": state, "content_sha256": digest},
            )
            if demo is not None:
                await asyncio.sleep(demo.timing.extraction_seconds)
                records, references = demo.instantiate(source.id)
                write_chunks = partial(
                    self.chunks.add_records_and_updates,
                    job.knowledge_base_id,
                    source.id,
                    records,
                    leading_keys=spec.embedding_context_keys,
                    references_by_record=references,
                )
                state.update(
                    stored=len(records),
                    merged=[],
                    consolidation={"demo_artifact": demo.title},
                    demo_artifact=demo.title,
                )
            elif mode == "knowledge":
                assert store is not None
                conversation = RawConversation(
                    id=source.id,
                    transcript=text,
                    stated_by=source.metadata.get("stated_by"),
                    metadata=source.metadata,
                    status=ConversationStatus.PENDING,
                    created_at=source.created_at,
                )
                result = await ingestion.ingest_stored(conversation)
                state.update(
                    stored=len(result.records),
                    merged=[{
                        "subject": record.subject,
                        "claim": record.claim,
                        "type": record.type.value,
                        "target": (record.revision.get("consolidation") or {}).get("target"),
                    } for record in result.merged],
                    consolidation=result.consolidation,
                )
                write_chunks = store.write_chunks
            else:
                assert store is not None
                passages = chunk_document(
                    Document(
                        id=str(source.id),
                        text=text,
                        title=source.title,
                        metadata={
                            "source_kind": source.metadata.get("source_kind", source.kind),
                            "transcript_sha256": transcript_sha256(text),
                        },
                    ),
                    spec.chunking,
                )
                vectors = await store.embed_texts([chunk.search_text for chunk in passages])
                references = references_for_chunks(
                    passages,
                    text,
                    segments_from_metadata(source.metadata),
                )
                write_chunks = partial(
                    self.chunks.add_passages,
                    job.knowledge_base_id,
                    source.id,
                    passages,
                    vectors,
                    references_by_chunk=references,
                )
                state["stored"] = len(passages)
        except asyncio.CancelledError:
            state.update(status="interrupted", finished_at=now())
            await asyncio.shield(self.sources.update(
                job.knowledge_base_id, source.id, metadata={"management": state}
            ))
            raise
        except Exception as exc:
            message, _retry = failure(exc)
            state.update(status="failed", finished_at=now(), error=message)
            await self.sources.update(
                job.knowledge_base_id, source.id, metadata={"management": state}
            )
            if run is not None:
                await runs.finish(run.id, "failed", error=message)
            raise
        state.update(status="done", finished_at=now())

        async def persist(session: AsyncSession) -> None:
            """Store chunks and finish the extraction with the job's transaction."""

            if write_chunks is not None:
                await write_chunks(session=session)
            await runs.finish(
                run.id, "done", session=session,
                result={"stored": state.get("stored", 0), "job_id": str(job.id)},
            )
            await self.sources.update(
                job.knowledge_base_id, source.id, session=session,
                metadata={"management": state},
            )

        return PreparedJobResult(
            result={"evidence_id": str(source.id), "mode": mode, "stored": state.get("stored", 0)},
            persist=persist,
        )

    async def run_conflict(self, job: Job) -> dict[str, Any]:
        """Judge suspected conflicts and return the summary for the job result."""

        _kb_name, spec, source = await self._source(job)
        if source.file is not None and load_demo_knowledge_extraction(
            source.title, source.file.content_type
        ) is not None:
            return {"skipped": "scripted demo extraction"}
        detector = spec.conflict_factory(spec) if spec.conflict_factory is not None else None
        if detector is None:
            return {"skipped": "conflict detection disabled"}
        store = self._store(job.knowledge_base_id, source, spec)
        conflicts = ConflictRepository(self.sessions, job.knowledge_base_id)
        return await detector.adetect(
            store,
            conflicts,
            source.id,
            job_id=str(job.id),
            persist=partial(self._persist_conflict, job, conflicts),
        )

    async def _persist_conflict(
        self, job: Job, conflicts: ConflictRepository, row: ConflictRow,
    ) -> ConflictRow | None:
        """Commit a verdict and its links under the job lease and both chunk locks."""

        async with conflicts.write_transaction() as session:
            await IngestionJobRepository(self.sessions).lock_lease(session, job)
            records = {
                record.id: record
                for record in await self.chunks.get_records(
                    job.knowledge_base_id, row.pair, session=session, for_update=True
                )
            }
            if len(records) < 2 or stale(row, records):
                return None
            previous = (await conflicts.current([row.pair], session=session)).get(row.pair)
            if previous is not None and not stale(previous, records) and (
                previous.reviewed
                or (previous.judge_fingerprint == row.judge_fingerprint and previous.status != "pending")
            ):
                return previous
            if row.status != "no_conflict" and any(
                record.status is KnowledgeStatus.DEPRECATED or record.superseded_by is not None
                for record in records.values()
            ):
                return None
            if row.status == "pending" and previous is not None:
                row = replace(row, linked=previous.linked)
            stored = await conflicts.put(row, session=session)
            if stored.status != "pending":
                event = {
                    "at": now(),
                    "action": "conflict_detected" if stored.linked else "conflict_cleared",
                    "relation": stored.relation,
                    "status": stored.status,
                    "aspect": stored.aspect,
                    "confidence": stored.confidence,
                    "reason": stored.reason,
                    "judge_fingerprint": stored.judge_fingerprint,
                    "job_id": stored.job_id,
                }
                updates = link_updates(
                    records[row.a_id], records[row.b_id], records, linked=stored.linked, event=event,
                )
                await self.chunks.apply_updates(job.knowledge_base_id, updates, session=session)
            return stored

    async def aclose(self) -> None:
        """Release cached client references; repositories own no persistent resources."""

        self._embeddings.clear()
