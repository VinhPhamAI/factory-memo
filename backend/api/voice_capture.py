"""Voice capture API: record a conversation, store it as evidence, extract knowledge.

Flow: the browser streams microphone PCM16 over `WS /v1/voice/{kb}/record`.
Every frame is saved to `data/audio/` (VOICE_AUDIO_DIR) and recognized live.
On stop the transcript is stored in the KB's `raw_conversations` table, then
knowledge extraction and (optionally) LLM transcript cleaning run in the
background while the reviewer reads the conversation. Extracted records stay
drafts until a reviewer approves or rejects them.

Job progress lives in memory and in the conversation metadata; a job that
was running when the API stopped is reported as interrupted and can be retried.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from backend.api.knowledge_bases import refresh_catalog
from backend.knowledge_bases.base import DATA_DIR, KnowledgeBaseSpec, Retrieval
from backend.src.services.knowledge_consolidation import KnowledgeConsolidator
from backend.src.services.knowledge_extraction import KnowledgeExtractionService
from backend.src.services.knowledge_ingestion import (
    KnowledgeIngestionService,
    transcript_sha256,
)
from backend.src.services.knowledge_verification import KnowledgeVerifier
from backend.src.services.speech_processing import SpeechClient, SpeechServiceError
from backend.src.services.transcript_cleaning import TranscriptCleaner
from backend.src.services.voice_capture import (
    CAPTURE_SOURCE_KINDS,
    MAX_TEXT_CHARS,
    SOURCE_KIND,
    TEXT_SOURCE_KIND,
    AudioRecorder,
    EmptyRecordingError,
    RecognitionOutcome,
    RecordingSession,
    audio_stem,
    build_turns,
    format_turns,
    parse_transcript,
    speaker_letters,
    text_turns,
    unique_path,
)
from backend.src.storage import KnowledgeRecord, KnowledgeStore, StorageError
from backend.src.storage.models import ConversationStatus, KnowledgeStatus


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/voice", tags=["voice"])

MAX_NAME_LENGTH = 120
MAX_SPEAKER_NAME_LENGTH = 60
MAX_UPLOAD_BYTES = 1 << 30
LIST_METADATA_KEYS = (
    "source_kind", "name", "recorded_at", "audio", "speakers", "cleaning", "ingestion",
)
REVIEW_ACTOR = "voice-capture review UI (unauthenticated)"
DECISIONS = {
    "approve": KnowledgeStatus.APPROVED,
    "reject": KnowledgeStatus.DEPRECATED,
    "reset": KnowledgeStatus.DRAFT,
}


def audio_dir_from_env() -> Path:
    raw = os.getenv("VOICE_AUDIO_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else DATA_DIR / "audio"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:1000]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def default_ingestion_factory(spec: KnowledgeBaseSpec, store: KnowledgeStore):
    """Same pipeline as `kb ingest`: extract, normalize, verify, consolidate.

    VOICE_VERIFY (off|quotes|llm, default quotes) and VOICE_CONSOLIDATE
    (default true) tune the advisory steps. A response that fails the output
    schema is asked for once more, so one bad field does not lose a recording.
    """

    from backend.scripts.kb import extraction_max_tokens_from_env

    extractor = KnowledgeExtractionService.from_env(
        spec.extract_prompt,
        max_tokens=extraction_max_tokens_from_env(),
        json_schema=spec.extraction_output_model is not None,
        output_model=spec.extraction_output_model,
        ontology=spec.ontology,
        validation_retries=1,
    )
    service = KnowledgeIngestionService(
        extractor, store,
        ontology=spec.ontology,
        normalizer=spec.record_normalizer,
        verifier=KnowledgeVerifier.from_env(os.getenv("VOICE_VERIFY", "quotes")),
        consolidator=(
            KnowledgeConsolidator.from_env(store)
            if _env_flag("VOICE_CONSOLIDATE", True) else None
        ),
    )
    provenance = {"version": spec.extraction_version, **extractor.provenance}
    return service, provenance


@dataclass
class Job:
    kind: Literal["ingestion", "cleaning"]
    task: asyncio.Task[None] | None = None
    stage: str = "queued"
    state: dict[str, Any] = field(default_factory=dict)


class VoiceCaptureService:
    """Stores, background jobs and audio files of voice capture, per knowledge base."""

    def __init__(
        self,
        specs: Mapping[str, KnowledgeBaseSpec],
        *,
        audio_dir: Path | None = None,
        speech_client: Callable[[], SpeechClient] = SpeechClient,
        open_store: Callable[[KnowledgeBaseSpec], Any] = KnowledgeStore.for_kb,
        ingestion_factory: Callable[[KnowledgeBaseSpec, Any], Any] = default_ingestion_factory,
        cleaner_factory: Callable[[], Any] = TranscriptCleaner.from_env,
    ) -> None:
        # Extraction needs a knowledge store; document-retrieval KBs do not search one.
        self._specs = {
            name: spec for name, spec in specs.items() if spec.retrieval is Retrieval.KNOWLEDGE
        }
        self.audio_dir = (audio_dir or audio_dir_from_env()).resolve()
        self.speech_client = speech_client
        self._open_store = open_store
        self._ingestion_factory = ingestion_factory
        self._cleaner_factory = cleaner_factory
        self._stores: dict[str, Any] = {}
        self._store_lock = asyncio.Lock()
        self._jobs: dict[tuple[UUID, str], Job] = {}

    # ----------------------------------------------------------- lookup

    def add(self, spec: KnowledgeBaseSpec) -> None:
        """Accepts recordings for a knowledge base created while the API runs."""

        if spec.retrieval is Retrieval.KNOWLEDGE:
            self._specs[spec.name] = spec

    def knowledge_bases(self) -> list[dict[str, str]]:
        return [
            {"name": name, "title": spec.title or name, "description": spec.description.strip()}
            for name, spec in sorted(self._specs.items())
        ]

    def spec(self, kb: str) -> KnowledgeBaseSpec:
        try:
            return self._specs[kb]
        except KeyError:
            raise HTTPException(404, f"Knowledge base {kb!r} does not support voice capture")

    async def store(self, kb: str) -> Any:
        spec = self.spec(kb)
        async with self._store_lock:
            if kb not in self._stores:
                self._stores[kb] = await self._open_store(spec)
        return self._stores[kb]

    async def conversation(self, kb: str, conversation_id: UUID):
        conversation = await (await self.store(kb)).get_conversation(conversation_id)
        if conversation is None or conversation.metadata.get("source_kind") not in CAPTURE_SOURCE_KINDS:
            raise HTTPException(404, "Conversation not found")
        return conversation

    def audio_path(self, metadata: Mapping[str, Any]) -> Path | None:
        name = (metadata.get("audio") or {}).get("file")
        if not name:
            return None
        path = (self.audio_dir / name).resolve()
        return path if path.parent == self.audio_dir and path.is_file() else None

    # ----------------------------------------------------------- saving

    async def save_recording(
        self,
        kb: str,
        *,
        name: str,
        recorded_at: datetime,
        recorder: AudioRecorder,
        outcome: RecognitionOutcome,
        clean: bool,
        capture_mode: Literal["live", "upload"],
        language: str,
        endpointing_ms: int,
    ) -> UUID:
        """Stores the transcript as evidence and starts extraction (and cleaning)."""

        transcript = outcome.transcript
        if not transcript.strip():
            raise EmptyRecordingError("no speech was recognized in the recording")
        store = await self.store(kb)
        turns = build_turns(outcome.utterances)
        sidecar = recorder.path.with_suffix(".asr.json")
        sidecar.write_text(json.dumps({
            "audio": recorder.path.name,
            "recognition": outcome.path,
            "realtime_error": outcome.realtime_error,
            "utterances": [utterance.to_json() for utterance in outcome.utterances],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        metadata = {
            "source_kind": SOURCE_KIND,
            "kb": kb,
            "name": name,
            "capture_mode": capture_mode,
            "recorded_at": recorded_at.isoformat(),
            "transcript_sha256": transcript_sha256(transcript),
            "audio": {
                "file": recorder.path.name,
                "asr_file": sidecar.name,
                "sha256": recorder.sha256(),
                "duration_s": round(recorder.duration_seconds, 2),
                "sample_rate": recorder.sample_rate,
                "format": "wav/pcm16/mono",
            },
            "asr": {
                "service": "nemo-speech.cpp",
                "language": language,
                "diarization": True,
                "endpointing_ms": endpointing_ms,
                "recognition": outcome.path,
                **({"realtime_error": outcome.realtime_error} if outcome.realtime_error else {}),
            },
            # Letters name voices in this recording; reviewers may add names.
            "speakers": {letter: {"name": None} for letter in speaker_letters(turns)},
            "turns": [turn.to_json() for turn in turns],
            "cleaning": {"enabled": clean, "status": "queued" if clean else "off"},
            "ingestion": {"status": "queued"},
        }
        conversation = await store.add_conversation(transcript, metadata=metadata)
        self._start_ingestion(kb, conversation)
        if clean:
            self.start_cleaning(kb, conversation.id, conversation.transcript)
        return conversation.id

    async def save_text(self, kb: str, *, name: str, text: str, dialogue: bool) -> UUID:
        """Stores a typed or pasted conversation as evidence and starts extraction.

        Typed text has no recognition noise, so it is not cleaned.
        """

        turns, names = text_turns(text, dialogue=dialogue)
        if not turns:
            raise ValueError("the text is empty")
        transcript = format_turns(turns)
        store = await self.store(kb)
        metadata = {
            "source_kind": TEXT_SOURCE_KIND,
            "kb": kb,
            "name": name,
            "capture_mode": "dialogue" if dialogue else "text",
            "recorded_at": _now(),
            "transcript_sha256": transcript_sha256(transcript),
            # Names typed before a colon are display labels, as a reviewer's would be.
            "speakers": {letter: {"name": names.get(letter)} for letter in speaker_letters(turns)},
            "turns": [turn.to_json() for turn in turns],
            "cleaning": {"enabled": False, "status": "off"},
            "ingestion": {"status": "queued"},
        }
        conversation = await store.add_conversation(transcript, metadata=metadata)
        self._start_ingestion(kb, conversation)
        return conversation.id

    # ----------------------------------------------------------- jobs

    def _spawn(self, key: tuple[UUID, str], job: Job, coroutine) -> None:
        self._jobs[key] = job
        job.task = asyncio.create_task(coroutine)

        def done(task: asyncio.Task[None]) -> None:
            if self._jobs.get(key) is job:
                del self._jobs[key]
            if not task.cancelled() and task.exception() is not None:
                logger.error("Voice %s job failed", job.kind, exc_info=task.exception())

        job.task.add_done_callback(done)

    def running(self, conversation_id: UUID, kind: str) -> Job | None:
        return self._jobs.get((conversation_id, kind))

    def _start_ingestion(self, kb: str, conversation) -> None:
        job = Job("ingestion")
        self._spawn((conversation.id, "ingestion"), job, self._ingest(kb, conversation, job))

    async def retry_ingestion(self, kb: str, conversation_id: UUID) -> None:
        conversation = await self.conversation(kb, conversation_id)
        if self.running(conversation_id, "ingestion"):
            raise HTTPException(409, "Extraction is already running")
        if conversation.status is ConversationStatus.PROCESSED:
            raise HTTPException(409, "Knowledge was already extracted from this conversation")
        store = await self.store(kb)
        if await store.knowledge_for_evidence(conversation_id):
            raise HTTPException(409, "This conversation already has knowledge records")
        await store.update_conversation_metadata(conversation_id, {"ingestion": {"status": "queued"}})
        self._start_ingestion(kb, conversation)

    async def _ingest(self, kb: str, conversation, job: Job) -> None:
        store = await self.store(kb)
        started = _now()
        job.stage = "extracting"

        def progress(stage: str) -> None:
            job.stage = stage

        try:
            # Built per run, so a missing LLM setting fails this job, not the recording.
            ingestion, extraction = self._ingestion_factory(self.spec(kb), store)
            await store.update_conversation_metadata(conversation.id, {
                "ingestion": {"status": "running", "started_at": started},
                "extraction": extraction,
            })
            result = await ingestion.ingest_stored(conversation, progress=progress)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Voice extraction failed for %s", conversation.id, exc_info=True)
            await store.update_conversation_metadata(conversation.id, {"ingestion": {
                "status": "failed", "started_at": started, "finished_at": _now(),
                "error": _error_text(exc),
            }})
            return
        await store.update_conversation_metadata(conversation.id, {"ingestion": {
            "status": "done", "started_at": started, "finished_at": _now(),
            "stored": len(result.records),
            # Lessons that restate stored knowledge add support links there instead.
            "merged": [
                {
                    "subject": record.subject,
                    "claim": record.claim,
                    "type": record.type.value,
                    "target": (record.revision.get("consolidation") or {}).get("target"),
                }
                for record in result.merged
            ],
            "consolidation": result.consolidation,
        }})

    def start_cleaning(self, kb: str, conversation_id: UUID, transcript: str) -> None:
        if self.running(conversation_id, "cleaning"):
            raise HTTPException(409, "Cleaning is already running")
        job = Job("cleaning")
        self._spawn((conversation_id, "cleaning"), job, self._clean(kb, conversation_id, transcript, job))

    async def _clean(self, kb: str, conversation_id: UUID, transcript: str, job: Job) -> None:
        store = await self.store(kb)
        turns = parse_transcript(transcript)
        state: dict[str, Any] = {
            "enabled": True, "status": "running", "started_at": _now(),
            "total": len(turns), "turns": {},
        }
        job.state = state
        job.stage = "running"
        try:
            cleaner = self._cleaner_factory()
            state["model"] = cleaner.provenance.get("model")
            await store.update_conversation_metadata(conversation_id, {"cleaning": state})

            async def on_batch(batch: dict[str, dict[str, Any]]) -> None:
                state["turns"].update(batch)
                await store.update_conversation_metadata(conversation_id, {"cleaning": state})

            await cleaner.aclean(turns, on_batch=on_batch)
            state.update(status="done", finished_at=_now())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Transcript cleaning failed for %s", conversation_id, exc_info=True)
            state.update(status="failed", finished_at=_now(), error=_error_text(exc))
        await store.update_conversation_metadata(conversation_id, {"cleaning": state})

    async def aclose(self) -> None:
        tasks = [job.task for job in self._jobs.values() if job.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        for store in self._stores.values():
            await store.aclose()
        self._stores.clear()

    # ----------------------------------------------------------- views

    def _job_status(self, conversation_id: UUID, kind: str, saved: Mapping[str, Any]) -> dict[str, Any]:
        status = dict(saved)
        job = self.running(conversation_id, kind)
        if job is not None:
            status["status"] = "running" if job.stage != "queued" else "queued"
            status["stage"] = job.stage
        elif status.get("status") in {"queued", "running"}:
            # Saved as running but no task: the API stopped while it ran.
            status["status"] = "interrupted"
        return status

    async def detail(self, kb: str, conversation_id: UUID) -> dict[str, Any]:
        conversation = await self.conversation(kb, conversation_id)
        metadata = conversation.metadata
        store = await self.store(kb)
        records = await store.knowledge_for_evidence(conversation_id)
        timings = {turn["id"]: turn for turn in metadata.get("turns") or []}
        turns = [
            {
                "id": turn.id,
                "speaker": turn.speaker,
                "text": turn.text,
                "start": (timings.get(turn.id) or {}).get("start"),
                "end": (timings.get(turn.id) or {}).get("end"),
            }
            for turn in parse_transcript(conversation.transcript)
        ]
        ingestion = self._job_status(conversation_id, "ingestion", metadata.get("ingestion") or {})
        if conversation.status is ConversationStatus.FAILED and ingestion.get("status") != "running":
            ingestion["status"] = "failed"
            ingestion.setdefault("error", conversation.error)
        merged_targets = [
            UUID(item["target"]) for item in ingestion.get("merged") or [] if item.get("target")
        ]
        cleaning = self._job_status(conversation_id, "cleaning", metadata.get("cleaning") or {})
        audio = metadata.get("audio") or {}
        return {
            "id": str(conversation.id),
            "kb": kb,
            "source": _source(metadata),
            "name": metadata.get("name") or "",
            "recorded_at": metadata.get("recorded_at"),
            "created_at": conversation.created_at.isoformat(),
            "status": conversation.status.value,
            "audio": {
                "available": self.audio_path(metadata) is not None,
                "file": audio.get("file"),
                "duration_s": audio.get("duration_s"),
            },
            "asr": metadata.get("asr") or {},
            "speakers": [
                {"label": letter, "name": (info or {}).get("name")}
                for letter, info in (metadata.get("speakers") or {}).items()
            ],
            "turns": turns,
            "cleaning": cleaning,
            "ingestion": ingestion,
            "records": [record_json(record) for record in records],
            "merged_into": [
                record_json(record) for record in await store.get_knowledge(merged_targets)
            ] if merged_targets else [],
        }

    async def conversations(self, kb: str, limit: int) -> list[dict[str, Any]]:
        rows = await (await self.store(kb)).list_conversations(
            metadata_filters=[{"source_kind": kind} for kind in CAPTURE_SOURCE_KINDS],
            metadata_keys=LIST_METADATA_KEYS,
            limit=limit,
        )
        items = []
        for row in rows:
            metadata = row["metadata"]
            conversation_id = row["id"]
            ingestion = self._job_status(conversation_id, "ingestion", metadata.get("ingestion") or {})
            if row["status"] == ConversationStatus.FAILED.value:
                ingestion["status"] = "failed"
            items.append({
                "id": str(conversation_id),
                "source": _source(metadata),
                "name": metadata.get("name") or "",
                "recorded_at": metadata.get("recorded_at"),
                "duration_s": (metadata.get("audio") or {}).get("duration_s"),
                "speakers": len(metadata.get("speakers") or {}),
                "ingestion": {"status": ingestion.get("status"), "stage": ingestion.get("stage")},
                "knowledge": row["knowledge_counts"],
            })
        return items


def _source(metadata: Mapping[str, Any]) -> str:
    return "text" if metadata.get("source_kind") == TEXT_SOURCE_KIND else "voice"


def record_json(record: KnowledgeRecord) -> dict[str, Any]:
    revision = record.revision or {}
    return {
        "id": str(record.id),
        "subject": record.subject,
        "claim": record.claim,
        "type": record.type.value,
        "status": record.status.value,
        "context": record.context,
        "stated_by": record.stated_by,
        "created_at": record.created_at.isoformat(),
        "valid_from": record.valid_from.isoformat() if record.valid_from else None,
        "valid_to": record.valid_to.isoformat() if record.valid_to else None,
        "supported_by": [str(i) for i in record.supported_by],
        "contradicted_by": [str(i) for i in record.contradicted_by],
        "conflict_with": [str(i) for i in record.conflict_with],
        "superseded_by": str(record.superseded_by) if record.superseded_by else None,
        "verify": revision.get("verify"),
        "ontology": revision.get("ontology"),
        "consolidation": revision.get("consolidation"),
        "history": revision.get("history") or [],
    }


# --------------------------------------------------------------- HTTP


def _service(request: Request | WebSocket) -> VoiceCaptureService:
    return request.app.state.voice_capture


def _storage_error(exc: StorageError) -> HTTPException:
    logger.warning("Voice capture storage error", exc_info=exc)
    return HTTPException(503, "The knowledge base database is unavailable")


class SpeakerNames(BaseModel):
    speakers: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("speakers")
    @classmethod
    def check(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        cleaned = {}
        for letter, name in value.items():
            name = (name or "").strip()
            if len(name) > MAX_SPEAKER_NAME_LENGTH:
                raise ValueError(f"speaker names are at most {MAX_SPEAKER_NAME_LENGTH} characters")
            cleaned[letter] = name or None
        return cleaned


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject", "reset"]
    note: str = Field(default="", max_length=1000)


@router.get("/knowledge-bases")
async def list_capture_knowledge_bases(request: Request) -> dict[str, list[dict[str, str]]]:
    await refresh_catalog(request)
    return {"knowledge_bases": _service(request).knowledge_bases()}


@router.get("/{kb}/conversations")
async def list_conversations(request: Request, kb: str, limit: int = 30) -> dict[str, Any]:
    try:
        return {"conversations": await _service(request).conversations(kb, limit)}
    except StorageError as exc:
        raise _storage_error(exc)


@router.get("/{kb}/conversations/{conversation_id}")
async def get_conversation(request: Request, kb: str, conversation_id: UUID) -> dict[str, Any]:
    try:
        return await _service(request).detail(kb, conversation_id)
    except StorageError as exc:
        raise _storage_error(exc)


@router.patch("/{kb}/conversations/{conversation_id}/speakers")
async def rename_speakers(
    request: Request, kb: str, conversation_id: UUID, body: SpeakerNames
) -> dict[str, Any]:
    service = _service(request)
    try:
        conversation = await service.conversation(kb, conversation_id)
        speakers = dict(conversation.metadata.get("speakers") or {})
        if unknown := sorted(set(body.speakers) - set(speakers)):
            raise HTTPException(422, f"Unknown speakers: {', '.join(unknown)}")
        for letter, name in body.speakers.items():
            speakers[letter] = {**(speakers[letter] or {}), "name": name}
        await (await service.store(kb)).update_conversation_metadata(
            conversation_id, {"speakers": speakers}
        )
        return await service.detail(kb, conversation_id)
    except StorageError as exc:
        raise _storage_error(exc)


@router.post("/{kb}/conversations/{conversation_id}/extract", status_code=202)
async def retry_extraction(request: Request, kb: str, conversation_id: UUID) -> dict[str, str]:
    try:
        await _service(request).retry_ingestion(kb, conversation_id)
    except StorageError as exc:
        raise _storage_error(exc)
    return {"status": "queued"}


@router.post("/{kb}/conversations/{conversation_id}/clean", status_code=202)
async def start_cleaning(request: Request, kb: str, conversation_id: UUID) -> dict[str, str]:
    service = _service(request)
    try:
        conversation = await service.conversation(kb, conversation_id)
        if _source(conversation.metadata) == "text":
            raise HTTPException(409, "Typed text is not cleaned")
        await (await service.store(kb)).update_conversation_metadata(
            conversation_id, {"cleaning": {"enabled": True, "status": "queued"}}
        )
    except StorageError as exc:
        raise _storage_error(exc)
    service.start_cleaning(kb, conversation_id, conversation.transcript)
    return {"status": "queued"}


@router.get("/{kb}/conversations/{conversation_id}/audio")
async def get_audio(request: Request, kb: str, conversation_id: UUID) -> FileResponse:
    service = _service(request)
    try:
        conversation = await service.conversation(kb, conversation_id)
    except StorageError as exc:
        raise _storage_error(exc)
    path = service.audio_path(conversation.metadata)
    if path is None:
        raise HTTPException(404, "The audio file is missing")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.post("/{kb}/knowledge/{record_id}/review")
async def review_knowledge(
    request: Request, kb: str, record_id: UUID, body: ReviewRequest
) -> dict[str, Any]:
    service = _service(request)
    status = DECISIONS[body.decision]
    try:
        store = await service.store(kb)
        record = await store.review_knowledge(record_id, status, event={
            "action": "reviewed",
            "decision": body.decision,
            "status": status.value,
            "note": body.note.strip() or None,
            "actor": REVIEW_ACTOR,
            "at": _now(),
        })
    except StorageError as exc:
        raise _storage_error(exc)
    if record is None:
        raise HTTPException(404, "Knowledge record not found, or replaced by a newer one")
    return record_json(record)


def _check_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Conversation name must be 1-{MAX_NAME_LENGTH} characters")
    return name


@router.post("/{kb}/recordings", status_code=201)
async def upload_recording(
    request: Request,
    kb: str,
    name: Annotated[str, Form()],
    audio: Annotated[UploadFile, File()],
    clean: Annotated[bool, Form()] = True,
    recorded_at: Annotated[datetime | None, Form()] = None,
) -> dict[str, str]:
    """A whole WAV recording, e.g. kept by the browser when the live socket dropped."""

    service = _service(request)
    service.spec(kb)
    try:
        name = _check_name(name)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    started = recorded_at or datetime.now(timezone.utc)
    path = unique_path(service.audio_dir, audio_stem(name, started.astimezone()), ".wav")
    service.audio_dir.mkdir(parents=True, exist_ok=True)
    size = 0
    with path.open("wb") as handle:
        while chunk := await audio.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                handle.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, "The recording is too large")
            handle.write(chunk)
    try:
        recorder = AudioRecorder.existing(path)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc))
    client = service.speech_client()
    try:
        utterances = await client.atranscribe_file(path)
        conversation_id = await service.save_recording(
            kb, name=name, recorded_at=started, recorder=recorder,
            outcome=RecognitionOutcome(utterances, "file"), clean=clean,
            capture_mode="upload", language=client.settings.language,
            endpointing_ms=client.settings.endpointing_ms,
        )
    except (SpeechServiceError, OSError) as exc:
        raise HTTPException(502, f"Speech recognition failed; the audio is kept as {path.name}") from exc
    except EmptyRecordingError as exc:
        raise HTTPException(422, f"{exc}; the audio is kept as {path.name}")
    except StorageError as exc:
        raise _storage_error(exc)
    return {"conversation_id": str(conversation_id)}


class TypedConversation(BaseModel):
    name: str
    text: str = Field(max_length=MAX_TEXT_CHARS)
    # Lines are "Name: text"; otherwise the whole text has one author.
    dialogue: bool = False


@router.post("/{kb}/texts", status_code=201)
async def add_text(request: Request, kb: str, body: TypedConversation) -> dict[str, str]:
    """A typed or pasted conversation, extracted like a recording."""

    service = _service(request)
    service.spec(kb)
    try:
        name = _check_name(body.name)
        conversation_id = await service.save_text(
            kb, name=name, text=body.text, dialogue=body.dialogue
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except StorageError as exc:
        raise _storage_error(exc)
    return {"conversation_id": str(conversation_id)}


# --------------------------------------------------------------- WebSocket


class StartMessage(BaseModel):
    type: Literal["start"]
    name: str
    sample_rate: int = Field(ge=8_000, le=96_000)
    clean: bool = True

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return _check_name(value)


@router.websocket("/{kb}/record")
async def record(websocket: WebSocket, kb: str) -> None:
    """Live recording. Client: {"type":"start",...}, PCM16 binary frames, {"type":"stop"}.

    Server: ready, partial, final, recognition_lost, saving, saved, error.
    """

    service = _service(websocket)
    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send(event: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(event)

    async def fail(message: str, code: str, audio_file: str | None = None) -> None:
        try:
            await send({"type": "error", "code": code, "message": message, "audio_file": audio_file})
            await websocket.close(code=1011 if code == "server" else 1008)
        except (WebSocketDisconnect, RuntimeError):
            pass

    if kb not in {item["name"] for item in service.knowledge_bases()}:
        await fail(f"Knowledge base {kb!r} does not support voice capture", "kb")
        return
    try:
        start = StartMessage.model_validate_json(await websocket.receive_text())
    except WebSocketDisconnect:
        return
    except ValueError as exc:
        await fail(f"Invalid start message: {exc}", "start")
        return

    client = service.speech_client()
    try:
        await client.aready()
    except Exception:
        logger.warning("Speech service unavailable", exc_info=True)
        await fail("The speech recognition service is not reachable", "speech_unavailable")
        return

    started = datetime.now(timezone.utc)
    recorder = AudioRecorder(
        unique_path(service.audio_dir, audio_stem(start.name, started.astimezone()), ".wav"),
        start.sample_rate,
    )
    session = RecordingSession(client, recorder, send)
    session.start()
    await send({"type": "ready", "audio_file": recorder.path.name})

    stopped = False
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                session.feed(message["bytes"])
            elif message.get("text"):
                command = json.loads(message["text"]).get("type")
                if command == "stop":
                    stopped = True
                    break
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass

    if not stopped:
        # Browser gone without stop: keep the audio, clearly unlinked from any conversation.
        await session.abort()
        interrupted = recorder.path.with_suffix(".interrupted.wav")
        recorder.path.rename(interrupted)
        logger.warning("Recording ended without stop; audio kept at %s", interrupted)
        return

    try:
        await send({"type": "saving"})
        outcome = await session.finish()
        conversation_id = await service.save_recording(
            kb, name=start.name, recorded_at=started, recorder=recorder, outcome=outcome,
            clean=start.clean, capture_mode="live", language=client.settings.language,
            endpointing_ms=client.settings.endpointing_ms,
        )
    except EmptyRecordingError as exc:
        await fail(f"{exc}. The audio is kept as {recorder.path.name}.", "empty", recorder.path.name)
        return
    except Exception as exc:
        logger.exception("Could not save recording %s", recorder.path)
        await session.abort()
        await fail(
            f"Could not save the conversation ({type(exc).__name__}). "
            f"The audio is kept as {recorder.path.name}.",
            "server",
            recorder.path.name,
        )
        return
    await send({"type": "saved", "conversation_id": str(conversation_id)})
    await websocket.close()
