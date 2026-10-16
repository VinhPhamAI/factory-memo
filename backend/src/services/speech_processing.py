"""Client for the NeMo-Speech.cpp speech service (ASR + speaker diarization).

The service (`speech-api` in deployments/docker-compose.yml) runs Nemotron 3.5
streaming ASR with Nemotron 3 diarization and server-side end-of-utterance
detection. Two ways to send audio:

- `SpeechClient.atranscribe_file` posts one complete WAV to
  `/v1/audio/transcriptions` and returns speaker turns.
- `SpeechClient.astream` sends PCM16 frames over the
  `/v1/audio/transcriptions/realtime` WebSocket and yields partial (`delta`)
  and final (`completed`) events; finals are split into speaker turns.

Speaker numbers are the diarizer's 1-based session labels, not identities;
words the diarizer did not tag keep `speaker=None` rather than a guessed label.
"""

from __future__ import annotations

import asyncio
import json
import os
import wave
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
from websockets.asyncio.client import connect


REALTIME_PATH = "/v1/audio/transcriptions/realtime"
TRANSCRIPTION_PATH = "/v1/audio/transcriptions"
_EVENT_PREFIX = "conversation.item.input_audio_transcription."


class SpeechServiceError(RuntimeError):
    """Raised when the speech service rejects a request or returns bad data."""


@dataclass(frozen=True)
class SpeechSettings:
    base_url: str = "http://127.0.0.1:8080"
    language: str = "vi-VN"
    endpointing_ms: int = 800
    timeout_seconds: float = 300.0

    @classmethod
    def from_env(cls) -> SpeechSettings:
        defaults = cls()
        return cls(
            base_url=os.getenv("SPEECH_API_URL") or defaults.base_url,
            language=os.getenv("SPEECH_LANGUAGE") or defaults.language,
            endpointing_ms=int(os.getenv("SPEECH_EOU_MS") or defaults.endpointing_ms),
            timeout_seconds=float(
                os.getenv("SPEECH_TIMEOUT_SECONDS") or defaults.timeout_seconds
            ),
        )


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    speaker: int | None = None
    confidence: float | None = None

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> Word:
        try:
            speaker = item.get("speaker")
            confidence = item.get("confidence")
            return cls(
                text=str(item["word"]),
                start=float(item["start"]),
                end=float(item["end"]),
                speaker=int(speaker) if speaker is not None else None,
                confidence=float(confidence) if confidence is not None else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechServiceError(f"malformed word in response: {item!r}") from exc


@dataclass(frozen=True)
class Utterance:
    """One speaker turn: consecutive words carrying the same speaker label."""

    text: str
    speaker: int | None = None
    start: float | None = None
    end: float | None = None
    words: tuple[Word, ...] = ()
    language: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "language": self.language,
            "words": [
                {
                    "word": word.text,
                    "start": word.start,
                    "end": word.end,
                    "speaker": word.speaker,
                    "confidence": word.confidence,
                }
                for word in self.words
            ],
        }


@dataclass(frozen=True)
class TranscriptEvent:
    kind: Literal["delta", "completed"]
    text: str
    utterances: tuple[Utterance, ...] = field(default=())
    audio_processed: float | None = None


def split_by_speaker(
    words: Sequence[Word], text: str = "", *, language: str | None = None
) -> list[Utterance]:
    """Groups consecutive words with the same speaker label into turns.

    Without word timings the whole text becomes one unlabelled turn. Word
    tokens are joined with spaces, so a turn's text can differ from the
    server's punctuated transcript in spacing only.
    """

    if not words:
        text = text.strip()
        return [Utterance(text=text, language=language)] if text else []
    turns: list[list[Word]] = []
    for word in words:
        if turns and turns[-1][-1].speaker == word.speaker:
            turns[-1].append(word)
        else:
            turns.append([word])
    return [
        Utterance(
            text=" ".join(word.text for word in turn).strip(),
            speaker=turn[0].speaker,
            start=turn[0].start,
            end=turn[-1].end,
            words=tuple(turn),
            language=language,
        )
        for turn in turns
    ]


def merge_turns(utterances: Iterable[Utterance]) -> list[Utterance]:
    """Joins adjacent turns of the same speaker (e.g. across EOU finals)."""

    merged: list[Utterance] = []
    for utterance in utterances:
        if not utterance.text:
            continue
        previous = merged[-1] if merged else None
        if previous is not None and previous.speaker == utterance.speaker:
            merged[-1] = Utterance(
                text=f"{previous.text} {utterance.text}",
                speaker=previous.speaker,
                start=previous.start if previous.start is not None else utterance.start,
                end=utterance.end if utterance.end is not None else previous.end,
                words=previous.words + utterance.words,
                language=previous.language or utterance.language,
            )
        else:
            merged.append(utterance)
    return merged


def speaker_label(speaker: int | None) -> str | None:
    """Letter for a diarizer label (1 -> A, 2 -> B, 27 -> AA); None when untagged.

    Letters only name a voice within one recording; they say nothing about
    who the person is.
    """

    if speaker is None or speaker < 1:
        return None
    label = ""
    while speaker:
        speaker, remainder = divmod(speaker - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def turn_id(number: int) -> str:
    """Citable id of the n-th (1-based) turn: T01, T02, ..., T100."""

    return f"T{number:02d}"


def format_transcript(utterances: Iterable[Utterance]) -> str:
    """`[T01] Speaker A: text` lines, the transcript form ingestion stores.

    Adjacent turns of one speaker are merged first; the [Tnn] ids let
    extracted evidence quotes cite the turn they come from.
    """

    lines = []
    for number, utterance in enumerate(merge_turns(utterances), start=1):
        letter = speaker_label(utterance.speaker)
        label = f"Speaker {letter}" if letter else "Unknown speaker"
        lines.append(f"[{turn_id(number)}] {label}: {utterance.text}")
    return "\n".join(lines)


def parse_transcription(payload: dict[str, Any]) -> list[Utterance]:
    """Speaker turns from a `verbose_json` transcription response."""

    if "text" not in payload:
        raise SpeechServiceError(f"transcription response has no text: {payload!r}")
    words = [Word.from_json(item) for item in payload.get("words") or []]
    return split_by_speaker(words, str(payload["text"]), language=payload.get("language"))


def read_wav_pcm16(path: str | Path) -> tuple[bytes, int]:
    """Mono little-endian PCM16 bytes and sample rate of a PCM16 WAV file.

    The service accepts 8-96 kHz, so the original rate is kept; stereo is
    averaged to mono.
    """

    with wave.open(str(path), "rb") as reader:
        if reader.getsampwidth() != 2 or reader.getcomptype() != "NONE":
            raise ValueError(f"{path}: expected uncompressed 16-bit PCM WAV")
        channels = reader.getnchannels()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).round().astype("<i2")
    return samples.tobytes(), rate


def iter_pcm_chunks(pcm: bytes, sample_rate: int, chunk_ms: int = 80) -> Iterator[bytes]:
    step = max(2, sample_rate * chunk_ms // 1000 * 2)
    for offset in range(0, len(pcm), step):
        yield pcm[offset : offset + step]


async def apace(chunks: Iterable[bytes], sample_rate: int) -> AsyncIterator[bytes]:
    """Yields PCM16 chunks no faster than real time, like a live microphone."""

    loop = asyncio.get_running_loop()
    started = loop.time()
    sent_seconds = 0.0
    for chunk in chunks:
        delay = started + sent_seconds - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        yield chunk
        sent_seconds += len(chunk) / 2 / sample_rate


async def _aiter(chunks: Iterable[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


class SpeechClient:
    def __init__(self, settings: SpeechSettings | None = None, *, connect_ws=connect) -> None:
        self.settings = settings or SpeechSettings.from_env()
        self._connect = connect_ws

    @property
    def _ws_url(self) -> str:
        base = self.settings.base_url.rstrip("/")
        scheme, _, rest = base.partition("://")
        return f"{'wss' if scheme == 'https' else 'ws'}://{rest}{REALTIME_PATH}"

    async def aget(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.settings.base_url, timeout=30.0
        ) as client:
            response = await client.get(path)
        return self._json(response)

    async def aready(self) -> dict[str, Any]:
        return await self.aget("/ready")

    async def atranscribe_file(
        self,
        path: str | Path,
        *,
        language: str | None = None,
        diarization: bool = True,
    ) -> list[Utterance]:
        path = Path(path)
        data = {
            "response_format": "verbose_json",
            "language": language or self.settings.language,
            "diarization": "true" if diarization else "false",
        }
        async with httpx.AsyncClient(
            base_url=self.settings.base_url, timeout=self.settings.timeout_seconds
        ) as client:
            with path.open("rb") as handle:
                response = await client.post(
                    TRANSCRIPTION_PATH,
                    data=data,
                    files={"file": (path.name, handle, "audio/wav")},
                )
        return parse_transcription(self._json(response))

    async def astream(
        self,
        pcm_chunks: AsyncIterable[bytes] | Iterable[bytes],
        *,
        sample_rate: int = 16000,
        language: str | None = None,
        diarization: bool = True,
        endpointing_ms: int | None = None,
    ) -> AsyncIterator[TranscriptEvent]:
        """Streams PCM16 mono audio and yields transcript events until commit.

        Audio is sent concurrently with reading results, so finals arrive while
        the speaker is still talking. After the last chunk the buffer is
        committed; the stream ends when the server acknowledges the commit.
        """

        if not isinstance(pcm_chunks, AsyncIterable):
            pcm_chunks = _aiter(pcm_chunks)
        language = language or self.settings.language
        session = {
            "sample_rate": sample_rate,
            "language": language,
            "automatic_punctuation": True,
            "word_timestamps": diarization,
            "speaker_diarization": diarization,
            "endpointing_ms": endpointing_ms or self.settings.endpointing_ms,
        }
        async with self._connect(
            self._ws_url, max_size=None, open_timeout=30
        ) as socket:
            await self._expect(socket, "session.created")
            await socket.send(json.dumps({"type": "session.update", "session": session}))
            await self._expect(socket, "session.updated")

            async def send_audio() -> None:
                async for chunk in pcm_chunks:
                    if chunk:
                        await socket.send(chunk)
                await socket.send(json.dumps({"type": "input_audio_buffer.commit"}))

            sender = asyncio.create_task(send_audio())
            try:
                while True:
                    event = await self._receive(socket, sender)
                    kind = event.get("type", "")
                    if kind == "input_audio_buffer.committed":
                        break
                    if kind == _EVENT_PREFIX + "delta":
                        yield TranscriptEvent(
                            "delta", str(event.get("delta", "")),
                            audio_processed=event.get("audio_processed"),
                        )
                    elif kind == _EVENT_PREFIX + "completed":
                        text = str(event.get("transcript", ""))
                        words = [Word.from_json(item) for item in event.get("words") or []]
                        yield TranscriptEvent(
                            "completed",
                            text,
                            tuple(split_by_speaker(words, text, language=language)),
                            event.get("audio_processed"),
                        )
                await sender
            finally:
                if not sender.done():
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)

    async def _receive(self, socket, sender: asyncio.Task | None = None) -> dict[str, Any]:
        """Next server event; fails early if sending audio failed meanwhile."""

        receive = asyncio.ensure_future(socket.recv())
        try:
            if sender is not None and not sender.done():
                await asyncio.wait(
                    {receive, sender},
                    timeout=self.settings.timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if sender.done() and sender.exception() is not None:
                    raise sender.exception()  # type: ignore[misc]
            message = await asyncio.wait_for(receive, self.settings.timeout_seconds)
        except TimeoutError as exc:
            raise SpeechServiceError("timed out waiting for the speech service") from exc
        finally:
            if not receive.done():
                receive.cancel()
        return self._event(message)

    async def _expect(self, socket, kind: str) -> dict[str, Any]:
        event = await self._receive(socket)
        if event.get("type") != kind:
            raise SpeechServiceError(f"expected {kind}, got {event.get('type')!r}")
        return event

    @staticmethod
    def _event(message: str | bytes) -> dict[str, Any]:
        try:
            event = json.loads(message)
        except (TypeError, ValueError) as exc:
            raise SpeechServiceError(f"non-JSON realtime message: {message!r:.200}") from exc
        if event.get("type") == "error":
            error = event.get("error") or {}
            raise SpeechServiceError(str(error.get("message") or error or "unknown error"))
        return event

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.is_error or not isinstance(payload, dict):
            message = response.text[:500]
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                message = payload["error"].get("message", message)
            raise SpeechServiceError(f"HTTP {response.status_code}: {message}")
        return payload
