"""Live voice capture: keep every audio frame, transcribe it into lettered turns.

A `RecordingSession` receives PCM16 mono frames from a microphone. Every
frame is appended to a WAV file first, then forwarded to the realtime speech
service, so the recording is complete even when recognition fails midway; in
that case the finished file is transcribed in one request instead.

Turns are numbered T01, T02, ... and speakers lettered A, B, C, ... in the
order the diarizer labels them. Letters name voices within one recording,
not people.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
import wave
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from backend.src.services.speech_processing import (
    SpeechClient,
    Utterance,
    format_transcript,
    merge_turns,
    speaker_label,
    turn_id,
)


logger = logging.getLogger(__name__)

SOURCE_KIND = "voice_recording"
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 96_000
# Waiting for the speech service to finish the last utterance after stop.
FINISH_TIMEOUT_SECONDS = 120.0
_TURN_LINE_RE = re.compile(r"^\[(T\d+)\] (?:Speaker ([A-Z]+)|Unknown speaker): ?(.*)$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def audio_stem(name: str, started_at: datetime) -> str:
    """File name stem from the conversation name: 20261015-143200_kiem-tra-truc-z."""

    folded = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    ascii_name = "".join(char for char in folded if not unicodedata.combining(char))
    slug = _SLUG_RE.sub("-", ascii_name.lower()).strip("-")[:60].strip("-")
    return f"{started_at:%Y%m%d-%H%M%S}_{slug or 'hoi-thoai'}"


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    path = directory / f"{stem}{suffix}"
    number = 2
    while path.exists():
        path = directory / f"{stem}-{number}{suffix}"
        number += 1
    return path


class EmptyRecordingError(ValueError):
    """The recording holds no recognized speech, so there is no transcript to store."""


class AudioRecorder:
    """Appends PCM16 mono frames to a WAV file whose header stays valid on disk."""

    def __init__(self, path: Path, sample_rate: int) -> None:
        if not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
            raise ValueError(
                f"sample rate must be {MIN_SAMPLE_RATE}-{MAX_SAMPLE_RATE} Hz, got {sample_rate}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.sample_rate = sample_rate
        self.frames = 0
        self._carry = b""
        self._writer: wave.Wave_write | None = wave.open(str(path), "wb")
        self._writer.setnchannels(1)
        self._writer.setsampwidth(2)
        self._writer.setframerate(sample_rate)

    @classmethod
    def existing(cls, path: Path) -> "AudioRecorder":
        """A closed recorder describing a finished PCM16 mono WAV file (e.g. an upload)."""

        try:
            with wave.open(str(path), "rb") as reader:
                if (reader.getsampwidth(), reader.getnchannels(), reader.getcomptype()) != (
                    2, 1, "NONE"
                ):
                    raise ValueError("expected a 16-bit PCM mono WAV file")
                rate, frames = reader.getframerate(), reader.getnframes()
        except (wave.Error, EOFError) as exc:
            raise ValueError(f"not a readable WAV file: {exc}") from exc
        recorder = cls.__new__(cls)
        recorder.path, recorder.sample_rate, recorder.frames = path, rate, frames
        recorder._carry, recorder._writer = b"", None
        return recorder

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate

    @property
    def closed(self) -> bool:
        return self._writer is None

    def write(self, chunk: bytes) -> bytes:
        """Appends whole samples (wave patches the header after each write).

        Returns the bytes written, so callers forward exactly what was kept.
        """

        if self._writer is None:
            raise RuntimeError("recording is closed")
        data = self._carry + chunk
        whole = len(data) - len(data) % 2
        self._carry = data[whole:]
        if whole:
            self._writer.writeframes(data[:whole])
            self.frames += whole // 2
        return data[:whole]

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def sha256(self) -> str:
        digest = hashlib.sha256()
        with self.path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()


@dataclass(frozen=True)
class CapturedTurn:
    id: str
    speaker: str | None
    text: str
    start: float | None = None
    end: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "speaker": self.speaker, "start": self.start, "end": self.end}


def build_turns(utterances: Iterable[Utterance]) -> list[CapturedTurn]:
    """Numbered turns, in the same order and merge as `format_transcript`."""

    return [
        CapturedTurn(
            id=turn_id(number),
            speaker=speaker_label(utterance.speaker),
            text=utterance.text,
            start=utterance.start,
            end=utterance.end,
        )
        for number, utterance in enumerate(merge_turns(utterances), start=1)
    ]


def parse_transcript(transcript: str) -> list[CapturedTurn]:
    """Turns of a stored `[T01] Speaker A: text` transcript (timings not included)."""

    turns = []
    for line in transcript.splitlines():
        match = _TURN_LINE_RE.match(line.strip())
        if match:
            turns.append(CapturedTurn(id=match[1], speaker=match[2], text=match[3]))
        elif turns and line.strip():
            # A line without an id continues the previous turn.
            last = turns[-1]
            turns[-1] = CapturedTurn(last.id, last.speaker, f"{last.text}\n{line.strip()}")
    return turns


def speaker_letters(turns: Sequence[CapturedTurn]) -> list[str]:
    return sorted({turn.speaker for turn in turns if turn.speaker}, key=lambda s: (len(s), s))


@dataclass
class RecognitionOutcome:
    utterances: list[Utterance]
    # "realtime": finals streamed while recording; "file": the whole WAV
    # transcribed after stop because realtime recognition failed.
    path: Literal["realtime", "file"]
    realtime_error: str | None = None

    @property
    def transcript(self) -> str:
        return format_transcript(self.utterances)


EventSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class RecordingSession:
    """One microphone recording: WAV file plus realtime recognition.

    `on_event` receives `partial` (text appended to the current utterance),
    `final` (finished speaker turns) and `recognition_lost` events.
    """

    client: SpeechClient
    recorder: AudioRecorder
    on_event: EventSink
    language: str | None = None
    finish_timeout: float = FINISH_TIMEOUT_SECONDS
    utterances: list[Utterance] = field(default_factory=list)
    realtime_error: str | None = None
    _queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    _task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._recognize())

    @property
    def recognizing(self) -> bool:
        return self._task is not None and not self._task.done()

    def feed(self, chunk: bytes) -> None:
        """Saves the frame, then hands it to recognition if that is still running."""

        kept = self.recorder.write(chunk)
        if kept and self.recognizing:
            self._queue.put_nowait(kept)

    async def finish(self) -> RecognitionOutcome:
        """Stops recording; returns every recognized utterance.

        Realtime finals are used when the stream ended cleanly; otherwise the
        saved WAV is transcribed as a whole, so no recorded speech is skipped.
        """

        self._queue.put_nowait(None)
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), self.finish_timeout)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self.realtime_error = "timed out waiting for the last utterance"
        self.recorder.close()
        if self.realtime_error is None:
            return RecognitionOutcome(list(self.utterances), "realtime")
        logger.warning(
            "Realtime recognition failed (%s); transcribing %s",
            self.realtime_error, self.recorder.path,
        )
        utterances = await self.client.atranscribe_file(
            self.recorder.path, language=self.language
        )
        return RecognitionOutcome(utterances, "file", self.realtime_error)

    async def abort(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.recorder.close()

    async def _frames(self) -> AsyncIterator[bytes]:
        while (chunk := await self._queue.get()) is not None:
            yield chunk

    async def _recognize(self) -> None:
        try:
            async for event in self.client.astream(
                self._frames(), sample_rate=self.recorder.sample_rate, language=self.language
            ):
                if event.kind == "delta":
                    if event.text:
                        await self.on_event({"type": "partial", "text": event.text})
                    continue
                turns = [utterance for utterance in event.utterances if utterance.text]
                self.utterances.extend(turns)
                await self.on_event({
                    "type": "final",
                    "turns": [
                        {
                            "speaker": speaker_label(turn.speaker),
                            "text": turn.text,
                            "start": turn.start,
                            "end": turn.end,
                        }
                        for turn in turns
                    ],
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.realtime_error = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning("Realtime recognition stopped", exc_info=True)
            # Frames keep going to the WAV file; drop the ones queued for ASR.
            while not self._queue.empty():
                self._queue.get_nowait()
            try:
                await self.on_event({"type": "recognition_lost"})
            except Exception:
                logger.debug("Could not report lost recognition", exc_info=True)
