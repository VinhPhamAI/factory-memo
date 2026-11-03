"""FastAPI app serving VieGem-ASR with the speech API the backend client uses.

Endpoints:
- `GET /health` (process up), `GET /ready` (model loaded), `GET /v1/models`.
- `POST /v1/audio/transcriptions`: one audio file; `response_format` is
  `json`, `text` or `verbose_json` (with `words` and `segments`). Files over
  30 s are cut into pieces at quiet points and transcribed as one batch.
- `POST /transcribe`: one file, returns `{"text": ...}`.
- WebSocket `/v1/audio/transcriptions/realtime`: `session.created`, then an
  optional `session.update`, binary PCM16 mono frames and
  `{"type": "input_audio_buffer.commit"}`; see `realtime.RealtimeSession`.

Speaker diarization is not provided; requests asking for it are refused.
Realtime sessions use voice activity detection (Silero VAD, `vad.py`) unless
`SPEECH_VAD=0` or the session sets `vad: false`. Whole files are transcribed
without it: the model is fast enough, and the VAD misses some quiet speech.
Settings come from the environment (see `Settings`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse

from .audio import MODEL_RATE, decode_file, split_long
from .engine import Engine, Transcription
from .realtime import MAX_VAD_THRESHOLD, MIN_VAD_THRESHOLD, RealtimeSession, SessionConfig
from .vad import SILERO_VERSION, ChunkVad, SileroVad

logger = logging.getLogger(__name__)

MODEL_ID = "5CD-AI/VieGem-ASR"


@dataclass(frozen=True)
class Settings:
    model_dir: str = "/models/VieGem-ASR"
    encoder_dir: str = "/models/embeddinggemma-2"
    device: str = "cuda"
    max_batch: int = 8
    batch_wait_ms: float = 5.0
    compile_model: bool = False
    language: str = "vi-VN"
    endpointing_ms: int = 800
    max_upload_mb: int = 200
    max_file_s: float = 3600.0
    vad: bool = True
    vad_threshold: float = 0.5

    @classmethod
    def from_env(cls) -> Settings:
        """Settings from `VIEGEM_*` and `SPEECH_*` variables; unset ones keep defaults."""

        d = cls()
        env = os.environ.get
        return cls(
            model_dir=env("VIEGEM_MODEL_DIR") or d.model_dir,
            encoder_dir=env("VIEGEM_ENCODER_DIR") or d.encoder_dir,
            device=env("SPEECH_DEVICE") or d.device,
            max_batch=int(env("SPEECH_MAX_BATCH") or d.max_batch),
            batch_wait_ms=float(env("SPEECH_BATCH_WAIT_MS") or d.batch_wait_ms),
            compile_model=env("SPEECH_COMPILE") == "1",
            language=env("SPEECH_LANGUAGE") or d.language,
            endpointing_ms=int(env("SPEECH_EOU_MS") or d.endpointing_ms),
            max_upload_mb=int(env("SPEECH_MAX_UPLOAD_MB") or d.max_upload_mb),
            max_file_s=float(env("SPEECH_MAX_FILE_S") or d.max_file_s),
            vad=env("SPEECH_VAD", "1") != "0",
            vad_threshold=float(env("SPEECH_VAD_THRESHOLD") or d.vad_threshold),
        )


def create_app(
    engine: Engine | None = None, settings: Settings | None = None,
    vad: ChunkVad | None = None,
) -> FastAPI:
    """Builds the app; tests pass a stub engine and VAD that are already loaded.

    The VAD serves realtime sessions and is used only when `settings.vad` is
    set; without one given, Silero VAD is loaded at startup.
    """

    settings = settings or Settings.from_env()
    engine = engine or Engine(
        settings.model_dir, settings.encoder_dir, device=settings.device,
        max_batch=settings.max_batch, batch_wait_s=settings.batch_wait_ms / 1000,
        compile_model=settings.compile_model,
    )
    if not MIN_VAD_THRESHOLD <= settings.vad_threshold <= MAX_VAD_THRESHOLD:
        raise ValueError(f"SPEECH_VAD_THRESHOLD must be {MIN_VAD_THRESHOLD}-{MAX_VAD_THRESHOLD}")
    vad_model = (vad or SileroVad()) if settings.vad else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if vad_model is not None and not vad_model.ready:
            await asyncio.to_thread(vad_model.load)
        if not engine.ready:
            await asyncio.to_thread(engine.load)
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(title="VieGem-ASR speech API", lifespan=lifespan,
                  description="Vietnamese speech recognition (5CD-AI/VieGem-ASR).")

    async def transcribe_file(data: bytes) -> tuple[Transcription, float]:
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"file larger than {settings.max_upload_mb} MB")
        try:
            audio = await asyncio.to_thread(decode_file, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        duration = len(audio) / MODEL_RATE
        if duration > settings.max_file_s:
            raise HTTPException(413, f"audio longer than {settings.max_file_s:.0f} s")
        pieces = split_long(audio)
        results = await asyncio.gather(*(engine.transcribe(clip) for _, clip in pieces))
        words = tuple(
            word.shifted(start / MODEL_RATE)
            for (start, _), result in zip(pieces, results)
            for word in result.words
        )
        return Transcription(" ".join(word.word for word in words), words), duration

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        if not engine.ready:
            return JSONResponse({"status": "loading"}, status_code=503)
        return {"status": "ready", "model": MODEL_ID, "device": settings.device,
                "vad": f"silero-vad {SILERO_VERSION}" if vad_model is not None else None}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{
            "id": MODEL_ID, "object": "model", "owned_by": "5CD-AI",
            "capabilities": ["transcription", "realtime_transcription", "word_timestamps",
                             *(["realtime_voice_activity_detection"]
                               if vad_model is not None else [])],
        }]}

    @app.post("/v1/audio/transcriptions", summary="Transcribe an audio file",
              description=TRANSCRIPTIONS_DOC, responses=TRANSCRIPTIONS_RESPONSES)
    async def transcriptions(
        file: UploadFile = File(..., description=(
            "Audio that libsndfile reads (WAV, FLAC, OGG, ...), any sample rate, mono "
            "or stereo. Not MP3 or M4A.")),
        model: str | None = Form(None, description="Ignored; accepted for OpenAI compatibility."),
        language: str | None = Form(None, description=(
            f"Echoed in `verbose_json` (default `{settings.language}`); it does not "
            "change recognition.")),
        response_format: str = Form("json", description=(
            "`json`: `{\"text\"}`; `text`: plain text; `verbose_json`: adds duration, "
            "words with times and confidence, and segments."),
            json_schema_extra={"enum": ["json", "text", "verbose_json"]}),
        diarization: bool = Form(False, description="Not supported; `true` gives 400."),
    ):
        if diarization:
            raise HTTPException(400, "speaker diarization is not supported")
        if response_format not in ("json", "text", "verbose_json"):
            raise HTTPException(400, "response_format must be json, text or verbose_json")
        result, duration = await transcribe_file(await file.read())
        if response_format == "text":
            return PlainTextResponse(result.text)
        if response_format == "json":
            return {"text": result.text}
        return {
            "task": "transcribe", "model": MODEL_ID,
            "language": language or settings.language,
            "duration": round(duration, 3), "text": result.text,
            "words": [word.to_json() for word in result.words],
            "segments": [{"id": 0, "start": result.words[0].start,
                          "end": result.words[-1].end, "text": result.text}]
            if result.words else [],
        }

    @app.post("/transcribe", summary="Transcribe an audio file (plain JSON)")
    async def transcribe(file: UploadFile = File(...)) -> dict[str, str]:
        result, _ = await transcribe_file(await file.read())
        return {"text": result.text}

    @app.websocket("/v1/audio/transcriptions/realtime")
    async def realtime(socket: WebSocket) -> None:
        await socket.accept()
        config = SessionConfig(
            language=settings.language, endpointing_ms=settings.endpointing_ms,
            vad=vad_model is not None, vad_threshold=settings.vad_threshold,
            vad_available=vad_model is not None,
        )
        session = RealtimeSession(engine.transcribe, config, vad=vad_model)
        audio_started = False

        async def send(events: list[dict[str, Any]]) -> None:
            for event in events:
                await socket.send_text(json.dumps(event, ensure_ascii=False))

        async def error(message: str) -> None:
            await send([{"type": "error", "error": {"message": message}}])

        await send([{"type": "session.created", "session": config.to_json()}])
        try:
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    audio_started = True
                    await send(await session.feed(message["bytes"]))
                    continue
                try:
                    event = json.loads(message.get("text") or "")
                except ValueError:
                    await error("expected a JSON event or binary PCM16 audio")
                    continue
                kind = event.get("type")
                if kind == "session.update":
                    if audio_started:
                        await error("session.update must come before audio")
                        continue
                    try:
                        config.update(event.get("session") or {})
                    except (TypeError, ValueError) as exc:
                        await error(str(exc))
                        continue
                    await send([{"type": "session.updated", "session": config.to_json()}])
                elif kind == "input_audio_buffer.commit":
                    await send(await session.commit())
                else:
                    await error(f"unsupported event type: {kind!r}")
        except WebSocketDisconnect:
            return
        except Exception as exc:
            logger.exception("Realtime session failed")
            try:
                await error(f"{type(exc).__name__}: {exc}")
                await socket.close(1011)
            except Exception:
                pass

    return app


TRANSCRIPTIONS_DOC = """\
Transcribes one audio file (OpenAI-compatible). Output is lowercase Vietnamese
without punctuation; numbers are written as words. Files over 30 s are cut at
quiet points and transcribed as one batch. Limits: 200 MB and one hour of audio
(`SPEECH_MAX_UPLOAD_MB`, `SPEECH_MAX_FILE_S`).

Live transcription uses the WebSocket `/v1/audio/transcriptions/realtime`
instead (not shown in OpenAPI; see AI_SERVICES_API_DOCS.md).
"""

TRANSCRIPTIONS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"description": "The transcript in the requested format.", "content": {
        "application/json": {"examples": {
            "json": {"value": {"text": "trục z kêu rè khi chạy nhanh"}},
            "verbose_json": {"value": {
                "task": "transcribe", "model": MODEL_ID, "language": "vi-VN",
                "duration": 2.4, "text": "trục z kêu rè",
                "words": [{"word": "trục", "start": 0.32, "end": 0.36, "confidence": 0.998},
                          {"word": "z", "start": 0.64, "end": 0.68, "confidence": 0.991}],
                "segments": [{"id": 0, "start": 0.32, "end": 1.6, "text": "trục z kêu rè"}],
            }},
        }},
        "text/plain": {"example": "trục z kêu rè khi chạy nhanh"},
    }},
    400: {"description": "Unreadable audio, unknown `response_format` or `diarization=true`."},
    413: {"description": "File over 200 MB or audio over one hour."},
}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.getenv("SPEECH_LOG_LEVEL", "INFO").upper())
    uvicorn.run(create_app(), host=os.getenv("SPEECH_HOST", "0.0.0.0"),
                port=int(os.getenv("SPEECH_PORT", "8080")))


if __name__ == "__main__":
    main()
