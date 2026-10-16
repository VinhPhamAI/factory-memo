"""Client for the NeMo-Speech.cpp speech service (ASR + speaker diarization).

The service (`speech-api` in deployments/docker-compose.yml) runs Nemotron 3.5
streaming ASR with Nemotron 3 diarization and server-side end-of-utterance
detection. Two ways to send audio:

- `SpeechClient.atranscribe_file` posts one complete WAV to
  `/v1/audio/transcriptions` and returns speaker turns.
- `SpeechClient.astream` sends PCM16 frames over the
  `/v1/audio/transcriptions/realtime` WebSocket and yields partial (`delta`)
  and final (`completed`) events; finals are split into speaker turns.
- `SpeechClient.atranscribe_segments` transcribes a long WAV in pieces cut at
  pauses, and `SpeechClient.adiarize` labels speakers over a whole file
  (`/v1/audio/diarizations`); `assign_speakers` joins the two.

The streaming model keeps its context for a whole session, and after a pause it
can stop emitting words for speech that follows (seen on Vietnamese speech: a
segment missed inside a 2-minute session was recognized when sent alone). So
long audio is recognized in short sessions or pieces, and speakers come from
one diarization pass over the whole recording, which keeps labels consistent.

Speaker numbers are the diarizer's 1-based session labels, not identities;
words the diarizer did not tag keep `speaker=None` rather than a guessed label.
"""

from __future__ import annotations

import asyncio
import bisect
import io
import json
import math
import os
import wave
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
from websockets.asyncio.client import connect


REALTIME_PATH = "/v1/audio/transcriptions/realtime"
TRANSCRIPTION_PATH = "/v1/audio/transcriptions"
DIARIZATION_PATH = "/v1/audio/diarizations"
# A frame is quiet when it is less than MARGIN above the noise floor and at
# least BELOW_SPEECH under the loud level, or below an absolute level (digital
# silence). The second condition keeps speech from counting as quiet when the
# recent audio is all speech and the "floor" is the speech level itself.
QUIET_MARGIN_DB = 10.0
QUIET_BELOW_SPEECH_DB = 12.0
QUIET_ABSOLUTE_DB = -55.0
# File pieces are cut in pauses at least SEGMENT_PAUSE_S long. Neighbours split
# by a pause shorter than JOIN_PAUSE_S are joined up to TARGET for context; a
# longer pause always separates pieces. No piece exceeds MAX.
SEGMENT_PAUSE_S = 0.5
JOIN_PAUSE_S = 1.5
SEGMENT_TARGET_S = 15.0
SEGMENT_MAX_S = 30.0
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
class SpeakerSegment:
    """A stretch of audio the diarizer gives to one speaker (1-based label)."""

    start: float
    end: float
    speaker: int


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


def shift(utterance: Utterance, seconds: float) -> Utterance:
    """The utterance with its own and its words' times moved by `seconds`."""

    if not seconds:
        return utterance
    move = lambda value: None if value is None else round(value + seconds, 3)  # noqa: E731
    return Utterance(
        text=utterance.text,
        speaker=utterance.speaker,
        start=move(utterance.start),
        end=move(utterance.end),
        words=tuple(
            Word(word.text, move(word.start), move(word.end), word.speaker, word.confidence)
            for word in utterance.words
        ),
        language=utterance.language,
    )


def _speaker_at(
    start: float, end: float, segments: Sequence[SpeakerSegment], starts: Sequence[float],
    max_gap: float,
) -> int | None:
    """Speaker of the segment overlapping [start, end] most, else the nearest one."""

    best, best_overlap, nearest, nearest_gap = None, 0.0, None, math.inf
    # Segments are sorted by start and rarely overlap, so few need checking.
    index = bisect.bisect_right(starts, end + max_gap)
    for segment in segments[max(0, index - 50):index]:
        overlap = min(end, segment.end) - max(start, segment.start)
        if overlap > best_overlap:
            best, best_overlap = segment.speaker, overlap
        gap = max(segment.start - end, start - segment.end, 0.0)
        if gap < nearest_gap:
            nearest, nearest_gap = segment.speaker, gap
    if best is not None:
        return best
    return nearest if nearest_gap <= max_gap else None


def assign_speakers(
    utterances: Sequence[Utterance], segments: Sequence[SpeakerSegment], *, max_gap: float = 1.0
) -> list[Utterance]:
    """Utterances relabelled from diarization segments over the same timeline.

    Each word takes the speaker whose segment it overlaps most, or the nearest
    segment within `max_gap` seconds; otherwise it stays unlabelled. Turns are
    re-split where the speaker changes, and speakers are renumbered by first
    appearance, so the first voice heard is 1 (A).
    """

    segments = sorted(segments, key=lambda segment: (segment.start, segment.end))
    starts = [segment.start for segment in segments]
    labelled: list[Utterance] = []
    for utterance in utterances:
        if utterance.words:
            words = [
                Word(word.text, word.start, word.end,
                     _speaker_at(word.start, word.end, segments, starts, max_gap),
                     word.confidence)
                for word in utterance.words
            ]
            labelled.extend(split_by_speaker(words, utterance.text, language=utterance.language))
        elif utterance.start is not None and utterance.end is not None:
            speaker = _speaker_at(utterance.start, utterance.end, segments, starts, max_gap)
            labelled.append(Utterance(utterance.text, speaker, utterance.start, utterance.end,
                                      (), utterance.language))
        else:
            labelled.append(Utterance(utterance.text, None, utterance.start, utterance.end,
                                      (), utterance.language))
    order: dict[int, int] = {}
    for utterance in labelled:
        if utterance.speaker is not None and utterance.speaker not in order:
            order[utterance.speaker] = len(order) + 1
    renumber = lambda speaker: None if speaker is None else order[speaker]  # noqa: E731
    return [
        Utterance(
            u.text, renumber(u.speaker), u.start, u.end,
            tuple(Word(w.text, w.start, w.end, renumber(w.speaker), w.confidence) for w in u.words),
            u.language,
        )
        for u in labelled
    ]


def _quiet(level: float, levels: Sequence[float]) -> bool:
    floor, loud = np.percentile(levels, [10, 90])
    return level < QUIET_ABSOLUTE_DB or (
        level < floor + QUIET_MARGIN_DB and level < loud - QUIET_BELOW_SPEECH_DB
    )


def frame_db(chunk: bytes) -> float:
    """RMS level of PCM16 audio in dBFS; -120 for empty or digital silence."""

    samples = np.frombuffer(chunk[: len(chunk) - len(chunk) % 2], dtype="<i2")
    if not samples.size:
        return -120.0
    rms = math.sqrt(float(np.mean(samples.astype(np.float64) ** 2)))
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


class SilenceTracker:
    """How long a PCM16 stream has been quiet, relative to its own noise floor.

    Levels are compared with the last `window_s` seconds (10th percentile as
    the floor, 90th as speech), so a noisy room is not taken for speech.
    """

    def __init__(self, sample_rate: int, window_s: float = 10.0) -> None:
        self._rate = sample_rate
        self._window = window_s
        self._levels: deque[tuple[float, float]] = deque()
        self._span = 0.0
        self.quiet_seconds = 0.0

    def add(self, chunk: bytes) -> None:
        seconds = len(chunk) / 2 / self._rate
        level = frame_db(chunk)
        self._levels.append((level, seconds))
        self._span += seconds
        while self._span > self._window and len(self._levels) > 1:
            self._span -= self._levels.popleft()[1]
        if _quiet(level, [value for value, _ in self._levels]):
            self.quiet_seconds += seconds
        else:
            self.quiet_seconds = 0.0


def speech_pieces(
    pcm: bytes, sample_rate: int, *, pause_s: float = SEGMENT_PAUSE_S,
    join_pause_s: float = JOIN_PAUSE_S, target_s: float = SEGMENT_TARGET_S,
    max_s: float = SEGMENT_MAX_S, frame_ms: int = 80,
) -> list[tuple[int, int]]:
    """Sample ranges to transcribe separately, cut in the middle of pauses.

    A stretch longer than `max_s` without such a pause is cut at its quietest
    frame in the last third. Stretches with no frame above the noise floor are
    left out. Neighbours separated by a pause shorter than `join_pause_s` are
    joined while the result stays within `target_s`.
    """

    frame = max(1, sample_rate * frame_ms // 1000)
    samples = len(pcm) // 2
    if not samples:
        return []
    levels = [frame_db(pcm[offset * 2:(offset + frame) * 2]) for offset in range(0, samples, frame)]
    floor, loud = np.percentile(levels, [10, 90])
    quiet = [
        level < QUIET_ABSOLUTE_DB
        or (level < floor + QUIET_MARGIN_DB and level < loud - QUIET_BELOW_SPEECH_DB)
        for level in levels
    ]
    pause_frames = max(1, round(pause_s * 1000 / frame_ms))
    max_frames = max(pause_frames * 2, round(max_s * 1000 / frame_ms))
    # Each cut with the length (in frames) of the pause it was made in.
    cuts, pauses, start, run = [0], [0], 0, 0
    for index, is_quiet in enumerate(quiet):
        run = run + 1 if is_quiet else 0
        if run >= pause_frames and not _quiet_next(quiet, index):
            cuts.append(index - run // 2)
            pauses.append(run)
            start, run = cuts[-1], 0
        elif index - start >= max_frames:
            window = range(start + (max_frames * 2) // 3, index)
            cut = min(window, key=lambda i: levels[i]) if window else index
            cuts.append(cut)
            pauses.append(0)
            start, run = cut, 0
    if run >= pause_frames:
        # Trailing silence: cut it off so it is not sent.
        cuts.append(len(levels) - run // 2)
        pauses.append(run)
    cuts.append(len(levels))
    join_frames = join_pause_s * 1000 / frame_ms
    target = target_s * sample_rate
    pieces: list[tuple[int, int]] = []
    joinable = False
    for first, last, pause in zip(cuts, cuts[1:], pauses):
        # A dropped all-quiet stretch in between is a long pause.
        joinable = joinable and pause < join_frames
        if last <= first or all(quiet[first:last]):
            joinable = False
            continue
        start, end = first * frame, min(samples, last * frame)
        if pieces and joinable and end - pieces[-1][0] <= target:
            pieces[-1] = (pieces[-1][0], end)
        else:
            pieces.append((start, end))
        joinable = True
    return pieces


def _quiet_next(quiet: Sequence[bool], index: int) -> bool:
    return index + 1 < len(quiet) and quiet[index + 1]


def uncovered_speech(
    utterances: Sequence[Utterance], segments: Sequence[SpeakerSegment], *,
    min_s: float = 0.6, margin_s: float = 0.3, join_s: float = 0.5,
) -> list[tuple[float, float]]:
    """Stretches the diarizer heard as speech but no recognized word covers.

    Words (or whole utterances without word times) cover their span plus
    `margin_s`; stretches closer than `join_s` are joined, and those shorter
    than `min_s` are ignored.
    """

    covered = sorted(
        (word.start - margin_s, word.end + margin_s)
        for utterance in utterances
        for word in (utterance.words or (
            [Word(utterance.text, utterance.start, utterance.end)]
            if utterance.start is not None and utterance.end is not None else []
        ))
    )
    gaps: list[tuple[float, float]] = []
    for segment in sorted(segments, key=lambda item: item.start):
        cursor = segment.start
        for start, end in covered:
            if end <= cursor or start >= segment.end:
                continue
            if start > cursor:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < segment.end:
            gaps.append((cursor, segment.end))
    joined: list[tuple[float, float]] = []
    for start, end in sorted(gaps):
        if joined and start - joined[-1][1] < join_s:
            joined[-1] = (joined[-1][0], max(joined[-1][1], end))
        else:
            joined.append((start, end))
    return [(round(a, 3), round(b, 3)) for a, b in joined if b - a >= min_s]


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


# A speaker's turn ends at a pause this long, or once it spans this long, so a
# monologue still gets citable turns that start near the quoted words.
TURN_PAUSE_S = 2.0
TURN_MAX_S = 30.0


def _continues(previous: Utterance, utterance: Utterance) -> bool:
    if previous.speaker != utterance.speaker:
        return False
    if utterance.start is not None and previous.end is not None:
        if utterance.start - previous.end >= TURN_PAUSE_S:
            return False
    if utterance.end is not None and previous.start is not None:
        if utterance.end - previous.start > TURN_MAX_S:
            return False
    return True


def merge_turns(utterances: Iterable[Utterance]) -> list[Utterance]:
    """Joins adjacent finals of one speaker into a turn (see TURN_PAUSE_S)."""

    merged: list[Utterance] = []
    for utterance in utterances:
        if not utterance.text:
            continue
        previous = merged[-1] if merged else None
        if previous is not None and _continues(previous, utterance):
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

    async def _post_file(
        self, path: str, name: str, content: bytes, data: dict[str, str]
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.settings.base_url, timeout=self.settings.timeout_seconds
        ) as client:
            response = await client.post(
                path, data=data, files={"file": (name, content, "audio/wav")}
            )
        return self._json(response)

    async def atranscribe_file(
        self,
        path: str | Path,
        *,
        language: str | None = None,
        diarization: bool = True,
    ) -> list[Utterance]:
        """One request for the whole file; long files may lose speech after
        pauses (see the module note), so prefer `atranscribe_segments`."""

        path = Path(path)
        return await self._transcribe(path.name, path.read_bytes(), language, diarization)

    async def _transcribe(
        self, name: str, wav: bytes, language: str | None, diarization: bool
    ) -> list[Utterance]:
        payload = await self._post_file(TRANSCRIPTION_PATH, name, wav, {
            "response_format": "verbose_json",
            "language": language or self.settings.language,
            "diarization": "true" if diarization else "false",
        })
        return parse_transcription(payload)

    async def atranscribe_segments(
        self, path: str | Path, *, language: str | None = None
    ) -> list[Utterance]:
        """The file transcribed piece by piece (cut at pauses), without speakers.

        Times are relative to the start of the file; label speakers with
        `adiarize` + `assign_speakers`.
        """

        pcm, rate = read_wav_pcm16(path)
        utterances: list[Utterance] = []
        for first, last in speech_pieces(pcm, rate):
            piece = pcm_to_wav(pcm[first * 2:last * 2], rate)
            for utterance in await self._transcribe("piece.wav", piece, language, False):
                if utterance.text:
                    utterances.append(shift(utterance, first / rate))
        return utterances

    async def arecover(
        self, path: str | Path, stretches: Sequence[tuple[float, float]], *,
        language: str | None = None,
    ) -> list[Utterance]:
        """Transcribes each (start, end) stretch of the file on its own."""

        pcm, rate = read_wav_pcm16(path)
        recovered: list[Utterance] = []
        for start, end in stretches:
            first, last = max(0, int(start * rate)), min(len(pcm) // 2, int(end * rate))
            if last <= first:
                continue
            piece = pcm_to_wav(pcm[first * 2:last * 2], rate)
            for utterance in await self._transcribe("gap.wav", piece, language, False):
                if utterance.text:
                    recovered.append(shift(utterance, first / rate))
        return recovered

    async def adiarize(
        self, wav: str | Path | bytes, *, mode: Literal["offline", "streaming"] = "offline"
    ) -> list[SpeakerSegment]:
        """Speaker segments over a whole recording (a WAV path or its bytes)."""

        content = wav if isinstance(wav, bytes) else Path(wav).read_bytes()
        payload = await self._post_file(DIARIZATION_PATH, "audio.wav", content, {"mode": mode})
        try:
            return [
                SpeakerSegment(float(item["start"]), float(item["end"]), int(item["speaker"]))
                for item in payload["segments"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechServiceError(f"malformed diarization response: {payload!r}") from exc

    async def astream(
        self,
        pcm_chunks: AsyncIterable[bytes] | Iterable[bytes],
        *,
        sample_rate: int = 16000,
        language: str | None = None,
        diarization: bool = True,
        endpointing_ms: int | None = None,
        word_timestamps: bool | None = None,
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
            "word_timestamps": diarization if word_timestamps is None else word_timestamps,
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
