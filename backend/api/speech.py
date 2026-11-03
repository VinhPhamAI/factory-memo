"""Speech-to-text for the chat composer.

The browser records a short WAV clip; this endpoint transcribes it through
the AI services gateway (`SPEECH_API_URL`, `POST /v1/audio/transcriptions`)
and returns plain text for the user to edit before sending. Nothing is stored.
See docs/features/conversations.md.
"""

from __future__ import annotations

import logging
import tempfile
import wave
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from backend.api.auth import require_current_user
from backend.src.services.auth import Account
from backend.src.services.speech_processing import SpeechClient, SpeechServiceError


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/speech", tags=["speech"])

MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_SECONDS = 120
_NOT_WAV = "Expected a PCM16 WAV recording at 8-96 kHz"
_UNAVAILABLE = "Speech recognition is temporarily unavailable"


class Transcript(BaseModel):
    """Recognized text; empty when no speech was found."""

    text: str


def get_chat_speech(request: Request) -> Any:
    """Return the injected speech client, or one configured from the environment."""

    speech = getattr(request.app.state, "chat_speech", None)
    if speech is None:
        try:
            speech = request.app.state.chat_speech = SpeechClient()
        except ValueError as exc:  # Malformed SPEECH_* numbers in the environment.
            logger.warning("Speech settings are invalid: %s", type(exc).__name__)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _UNAVAILABLE) from exc
    return speech


async def _read_bounded(file: UploadFile) -> bytes:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The recording is too large")
    return content


def _check_wav(path: Path) -> None:
    """Accept only uncompressed PCM16 WAV of at most MAX_SECONDS."""

    try:
        with wave.open(str(path), "rb") as reader:
            valid = (
                reader.getsampwidth() == 2
                and reader.getcomptype() == "NONE"
                # The speech service accepts 8-96 kHz.
                and 8000 <= reader.getframerate() <= 96000
            )
            seconds = reader.getnframes() / reader.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, _NOT_WAV) from exc
    if not valid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, _NOT_WAV)
    if seconds > MAX_SECONDS:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"Recordings are limited to {MAX_SECONDS} seconds"
        )


@router.post("/transcriptions")
async def transcribe(
    file: UploadFile,
    _user: Annotated[Account, Depends(require_current_user)],
    speech: Annotated[Any, Depends(get_chat_speech)],
) -> Transcript:
    """Transcribe one recording (PCM16 WAV, at most 120 s) to editable text.

    Raises:
        HTTPException: 413 too large or long, 422 not a PCM16 WAV, 503 when
            the speech service failed or is unreachable.
    """

    content = await _read_bounded(file)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recording.wav"
        path.write_bytes(content)
        _check_wav(path)
        try:
            utterances = await speech.atranscribe_segments(path)
        except ValueError as exc:  # The WAV could not be read as PCM16.
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, _NOT_WAV) from exc
        except (SpeechServiceError, httpx.HTTPError, OSError) as exc:
            # Gateway messages can name internal hosts; log the class only.
            logger.warning("Chat transcription failed: %s", type(exc).__name__)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _UNAVAILABLE) from exc
    return Transcript(text=" ".join(item.text.strip() for item in utterances if item.text.strip()))
