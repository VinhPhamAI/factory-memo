"""Live voice capture: keep every audio frame, transcribe it into lettered turns.

A `RecordingSession` receives PCM16 mono frames from a microphone. Every
frame is appended to a WAV file first, then forwarded to the realtime speech
service, so the recording is complete even when recognition fails midway; in
that case the finished file is transcribed piece by piece instead.

Recognition runs in short realtime sessions: after a final, the next quiet
moment closes the session and a new one continues, because one long session
can stop recognizing speech that follows a pause (see speech_processing).
Speakers come from diarizing the WAV recorded so far, so letters stay the same
across sessions; the stored transcript is labelled from the whole file.

Turns are numbered T01, T02, ... and speakers lettered A, B, C, ... in the
order the diarizer labels them. Letters name voices within one recording,
not people.

A conversation can also be typed or pasted instead of recorded (`text_turns`);
it gets the same transcript format, review and extraction, without audio.
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
    SilenceTracker,
    SpeakerSegment,
    assign_speakers,
    shift,
    uncovered_speech,
    SpeechClient,
    Utterance,
    format_transcript,
    merge_turns,
    speaker_label,
    turn_id,
)


logger = logging.getLogger(__name__)

SOURCE_KIND = "voice_recording"
TEXT_SOURCE_KIND = "typed_text"
# Conversations the capture UI lists and reviews.
CAPTURE_SOURCE_KINDS = (SOURCE_KIND, TEXT_SOURCE_KIND)
MAX_TEXT_CHARS = 100_000
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 96_000
# Waiting for the speech service to finish the last utterance after stop.
FINISH_TIMEOUT_SECONDS = 120.0
# Quiet audio needed after a final before the realtime session is renewed; the
# new session is primed with up to PRIME seconds of that quiet audio, because a
# session starting right on speech can miss its first words.
ROTATE_QUIET_SECONDS = 0.6
PRIME_SECONDS = 0.5
# Live speaker labels are refreshed at most this often, and less often for long
# recordings (the WAV so far is re-sent each time).
RELABEL_MIN_SECONDS = 4.0
RELABEL_SHARE = 1 / 30
_TURN_LINE_RE = re.compile(r"^\[(T\d+)\] (?:Speaker ([A-Z]+)|Unknown speaker): ?(.*)$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# "Anh Minh: text" — a short speaker name before a colon.
_NAMED_LINE_RE = re.compile(r"^([^\s:\[][^:]{0,29}?)\s*:\s+(\S.*)$")
_LETTER_NAME_RE = re.compile(r"^(?:Speaker\s+|Người nói\s+)?([A-Z])$", re.IGNORECASE)
_BLANK_LINE_RE = re.compile(r"\n\s*\n")


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


def format_turns(turns: Sequence[CapturedTurn]) -> str:
    """The stored transcript of turns, in `format_transcript`'s line format."""

    return "\n".join(
        f"[{turn.id}] {f'Speaker {turn.speaker}' if turn.speaker else 'Unknown speaker'}: {turn.text}"
        for turn in turns
    )


def text_turns(
    text: str, *, dialogue: bool = False
) -> tuple[list[CapturedTurn], dict[str, str | None]]:
    """Turns of a typed conversation, and the speaker name behind each letter.

    Nothing is reworded. A transcript already in `[T01] Speaker A: text` form
    keeps its turns (renumbered). With `dialogue`, each "Name: text" line starts
    a turn, names are lettered A, B, ... in order of appearance and kept as
    display names, and other lines continue the turn above. Otherwise the text
    has one author, Speaker A: one turn per paragraph, or per line when there
    are no blank lines. Raises ValueError for a dialogue whose first line has
    no name.
    """

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return [], {}
    if _TURN_LINE_RE.match(lines[0]):
        parsed = parse_transcript(text)
        turns = [CapturedTurn(turn_id(n), turn.speaker, turn.text.strip())
                 for n, turn in enumerate(parsed, start=1) if turn.text.strip()]
        return turns, {letter: None for letter in speaker_letters(turns)}
    if dialogue:
        letters: dict[str, str] = {}
        names: dict[str, str | None] = {}
        said: list[tuple[str, str]] = []
        for line in lines:
            match = _NAMED_LINE_RE.match(line)
            if match is None:
                if not said:
                    raise ValueError('the first line must start with a speaker name, e.g. "Anh Minh: ..."')
                said[-1] = (said[-1][0], f"{said[-1][1]}\n{line}")
                continue
            key = " ".join(match[1].split()).casefold()
            if key not in letters:
                letters[key] = speaker_label(len(letters) + 1)
                # "A" or "Speaker B" is a label, not a name.
                names[letters[key]] = None if _LETTER_NAME_RE.match(match[1]) else match[1].strip()
            said.append((letters[key], match[2].strip()))
        return [CapturedTurn(turn_id(n), letter, words)
                for n, (letter, words) in enumerate(said, start=1)], names
    blocks = _BLANK_LINE_RE.split(text) if _BLANK_LINE_RE.search(text) else lines
    paragraphs = ["\n".join(line.strip() for line in block.split("\n") if line.strip())
                  for block in blocks]
    turns = [CapturedTurn(turn_id(number), "A", paragraph)
             for number, paragraph in enumerate((p for p in paragraphs if p), start=1)]
    return turns, {"A": None}


@dataclass
class RecognitionOutcome:
    utterances: list[Utterance]
    # "realtime": finals streamed while recording; "file": the WAV transcribed
    # piece by piece after stop because realtime recognition failed.
    path: Literal["realtime", "file"]
    realtime_error: str | None = None
    # Why speakers could not be labelled; the turns then have no letters.
    diarization_error: str | None = None
    # Realtime sessions used (renewed after finals).
    sessions: int = 0
    # Utterances added from speech the diarizer heard but recognition missed.
    recovered: int = 0

    @property
    def transcript(self) -> str:
        return format_transcript(self.utterances)


EventSink = Callable[[dict[str, Any]], Awaitable[None]]


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


async def complete_and_label(
    client: SpeechClient, wav: Path, utterances: list[Utterance], *,
    language: str | None = None,
) -> tuple[list[Utterance], str | None, int]:
    """Diarizes the whole file, transcribes speech no utterance covers, and
    labels speakers. Returns the utterances, the diarization error (turns then
    have no letters) and how many utterances were recovered."""

    try:
        segments = await client.adiarize(wav)
    except Exception as exc:
        logger.warning("Diarization of %s failed", wav, exc_info=True)
        return [Utterance(u.text, None, u.start, u.end, u.words, u.language)
                for u in utterances], _error_text(exc), 0
    recovered: list[Utterance] = []
    try:
        if gaps := uncovered_speech(utterances, segments):
            recovered = await client.arecover(wav, gaps, language=language)
            logger.info("Recovered %d utterances from %d missed stretches of %s",
                        len(recovered), len(gaps), wav.name)
    except Exception:
        logger.warning("Recovering missed speech in %s failed", wav, exc_info=True)
    merged = sorted([*utterances, *recovered], key=lambda u: (u.start is None, u.start or 0.0))
    return assign_speakers(merged, segments), None, len(recovered)


async def recognize_file(
    client: SpeechClient, wav: Path, *, language: str | None = None
) -> tuple[list[Utterance], str | None, int]:
    """Whole-file recognition: pieces cut at pauses, missed speech recovered,
    speakers from diarization."""

    utterances = await client.atranscribe_segments(wav, language=language)
    return await complete_and_label(client, wav, utterances, language=language)


def turns_json(utterances: Sequence[Utterance]) -> list[dict[str, Any]]:
    return [
        {"id": turn.id, "speaker": turn.speaker, "text": turn.text, "start": turn.start,
         "end": turn.end}
        for turn in build_turns(utterances)
    ]


@dataclass
class RecordingSession:
    """One microphone recording: WAV file plus realtime recognition.

    `on_event` receives `partial` (text appended to the utterance in progress),
    `turns` (every turn so far, numbered as the stored transcript will be, with
    speaker letters once diarization has seen them) and `recognition_lost`.
    """

    client: SpeechClient
    recorder: AudioRecorder
    on_event: EventSink
    language: str | None = None
    finish_timeout: float = FINISH_TIMEOUT_SECONDS
    # Recognized utterances on the recording's timeline, without speakers.
    utterances: list[Utterance] = field(default_factory=list)
    realtime_error: str | None = None
    sessions: int = 0
    _queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    _task: asyncio.Task[None] | None = None
    _carry: bytes | None = None
    # Quiet frames that ended the previous session; they open the next one.
    _prime: list[bytes] = field(default_factory=list)
    _ended: bool = False
    _sent: int = 0
    _segments: list[SpeakerSegment] = field(default_factory=list)
    _relabel_task: asyncio.Task[None] | None = None
    _relabel_wanted: bool = False
    _relabelled_at: float = -1e9

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
        """Stops recording; returns every recognized utterance with speakers.

        Realtime finals are used when recognition ran to the end; otherwise the
        saved WAV is transcribed piece by piece, so no recorded speech is
        skipped. Speakers come from diarizing the complete file.
        """

        self._queue.put_nowait(None)
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), self.finish_timeout)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self.realtime_error = "timed out waiting for the last utterance"
        await self._stop_relabel()
        self.recorder.close()
        if self.realtime_error is None:
            utterances, path = list(self.utterances), "realtime"
        else:
            logger.warning(
                "Realtime recognition failed (%s); transcribing %s",
                self.realtime_error, self.recorder.path,
            )
            utterances = await self.client.atranscribe_segments(
                self.recorder.path, language=self.language
            )
            path = "file"
        labelled, diarization_error, recovered = await complete_and_label(
            self.client, self.recorder.path, utterances, language=self.language
        )
        return RecognitionOutcome(
            labelled, path, self.realtime_error, diarization_error, self.sessions, recovered
        )

    async def abort(self) -> None:
        for task in (self._task, self._relabel_task):
            if task is not None and not task.done():
                task.cancel()
                # Do not let a stuck speech connection hold the recording open.
                await asyncio.wait({task}, timeout=2.0)
        self.recorder.close()

    async def _next_frame(self) -> bytes | None:
        chunk, self._carry = self._carry, None
        if chunk is None:
            chunk = await self._queue.get()
        if chunk is None:
            self._ended = True
        return chunk

    async def _frames(self, renew: Callable[[], bool]) -> AsyncIterator[bytes]:
        """Frames for one realtime session; ends at stop, or at a quiet moment
        once `renew()` says the session has given a final."""

        rate = self.recorder.sample_rate
        silence = SilenceTracker(rate)
        prime, self._prime = self._prime, []
        for chunk in prime:
            silence.add(chunk)
            yield chunk
        tail: list[bytes] = []
        while (chunk := await self._next_frame()) is not None:
            if renew() and silence.quiet_seconds >= ROTATE_QUIET_SECONDS:
                # The next session starts with this frame, after the quiet tail.
                self._carry = chunk
                self._prime = tail
                return
            silence.add(chunk)
            if silence.quiet_seconds:
                tail.append(chunk)
                while sum(len(c) for c in tail) / 2 / rate > PRIME_SECONDS and len(tail) > 1:
                    tail.pop(0)
            else:
                tail = []
            self._sent += len(chunk) // 2
            yield chunk

    async def _recognize(self) -> None:
        rate = self.recorder.sample_rate
        try:
            while True:
                # Open a session only when there is audio for it.
                first = await self._next_frame()
                if first is None:
                    break
                self._carry = first
                # Session time 0 is the start of its priming audio.
                offset = (self._sent - sum(len(c) for c in self._prime) // 2) / rate
                finals: list[Utterance] = []
                self.sessions += 1
                logger.debug("Realtime session %d starts at %.2fs", self.sessions, offset)
                async for event in self.client.astream(
                    self._frames(lambda: bool(finals)), sample_rate=rate,
                    language=self.language, diarization=False, word_timestamps=True,
                ):
                    if event.kind == "delta":
                        if event.text:
                            await self.on_event({"type": "partial", "text": event.text})
                        continue
                    new = [shift(u, offset) for u in event.utterances if u.text]
                    logger.debug("Final at %.2fs: %r", offset + (event.audio_processed or 0),
                                 event.text)
                    if new:
                        finals.extend(new)
                        self.utterances.extend(new)
                        await self._publish()
                        self._schedule_relabel()
                if self._ended:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.realtime_error = _error_text(exc)
            logger.warning("Realtime recognition stopped", exc_info=True)
            # Frames keep going to the WAV file; drop the ones queued for ASR.
            self._carry = None
            while not self._queue.empty():
                self._queue.get_nowait()
            try:
                await self.on_event({"type": "recognition_lost"})
            except Exception:
                logger.debug("Could not report lost recognition", exc_info=True)

    # ------------------------------------------------------- live speakers

    async def _publish(self) -> None:
        utterances = (
            assign_speakers(self.utterances, self._segments) if self._segments
            else self.utterances
        )
        await self.on_event({"type": "turns", "turns": turns_json(utterances)})

    def _schedule_relabel(self) -> None:
        self._relabel_wanted = True
        if self._relabel_task is None or self._relabel_task.done():
            self._relabel_task = asyncio.create_task(self._relabel())

    async def _relabel(self) -> None:
        """Diarizes the WAV so far after new finals, at a bounded rate."""

        loop = asyncio.get_running_loop()
        while self._relabel_wanted:
            self._relabel_wanted = False
            interval = max(RELABEL_MIN_SECONDS, self.recorder.duration_seconds * RELABEL_SHARE)
            wait = self._relabelled_at + interval - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._relabelled_at = loop.time()
            try:
                # Read in the event loop, between writes, so the header matches the data.
                self._segments = await self.client.adiarize(self.recorder.path.read_bytes())
                await self._publish()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Live letters are a preview; the saved transcript is diarized again.
                logger.debug("Live diarization failed", exc_info=True)

    async def _stop_relabel(self) -> None:
        if self._relabel_task is not None and not self._relabel_task.done():
            self._relabel_task.cancel()
            await asyncio.gather(self._relabel_task, return_exceptions=True)
