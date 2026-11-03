"""Realtime transcription session: PCM16 frames in, partial and final events out.

The model is not run incrementally. The session keeps the audio of the
utterance in progress and re-transcribes all of it after every
`partial_interval_s` of new audio (the model runs far faster than real time):

- Endpointing uses the model itself: once the last recognized word was
  emitted at least `endpointing_ms` before the end of the buffer, the
  utterance is final and the buffer is cut in the middle of that pause.
  Background noise that yields no words never ends an utterance. CTC emits a
  word near its start, so the pause measured includes the rest of that word.
- Partials are append-only, as clients join them: all words except the last
  (which may still change) are sent once they extend what was already sent.
  After a revision no more partials are sent for that utterance; its final
  holds the corrected text.
- An utterance reaching `max_utterance_s` is cut at the largest pause between
  its words, so no clip exceeds the model's 30 s.
- Audio without any word is dropped, keeping `keep_s` before the next speech.

With voice activity detection (Silero VAD, on by default) every frame is also
rated as it arrives. The VAD only changes when the model runs; words and audio
are never dropped on its word alone, because it misses some quiet speech
(see `vad.py`):

- While the VAD hears no speech in the buffer and the last run found no
  words, the model runs every `idle_interval_s` instead of every
  `partial_interval_s`. Speech the VAD misses is still recognized, later.
- Once the VAD has heard `endpointing_ms` without speech after a region, the
  model runs at once instead of at the next interval, so the final comes sooner.
- `input_audio_buffer.speech_started` / `speech_stopped` report region edges
  (`audio_start_ms` / `audio_end_ms`).

Times in events are seconds since the session's first audio frame.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .audio import pcm16_to_float, resample
from .engine import Transcription, Word
from .vad import DEFAULT_THRESHOLD, ChunkVad, VadStream

EVENT_PREFIX = "conversation.item.input_audio_transcription."
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 96_000
MIN_ENDPOINTING_MS = 200
MAX_ENDPOINTING_MS = 5_000
MIN_VAD_THRESHOLD = 0.1
MAX_VAD_THRESHOLD = 0.9

Transcribe = Callable[[np.ndarray], Awaitable[Transcription]]


@dataclass
class SessionConfig:
    sample_rate: int = 16_000
    language: str = "vi-VN"
    endpointing_ms: int = 800
    word_timestamps: bool = True
    vad: bool = True
    vad_threshold: float = DEFAULT_THRESHOLD
    # False when the server runs without a VAD; sessions cannot turn it on.
    vad_available: bool = True

    def update(self, values: dict[str, Any]) -> None:
        """Applies a client `session.update`; unknown keys are ignored.

        Raises:
            ValueError: A value is out of range or of the wrong type.
        """

        if "speaker_diarization" in values and values["speaker_diarization"]:
            raise ValueError("speaker diarization is not supported")
        if "sample_rate" in values:
            rate = int(values["sample_rate"])
            if not MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE:
                raise ValueError(f"sample_rate must be {MIN_SAMPLE_RATE}-{MAX_SAMPLE_RATE}")
            self.sample_rate = rate
        if values.get("language"):
            self.language = str(values["language"])
        if values.get("endpointing_ms") is not None:
            ms = int(values["endpointing_ms"])
            if not MIN_ENDPOINTING_MS <= ms <= MAX_ENDPOINTING_MS:
                raise ValueError(
                    f"endpointing_ms must be {MIN_ENDPOINTING_MS}-{MAX_ENDPOINTING_MS}"
                )
            self.endpointing_ms = ms
        if "word_timestamps" in values:
            self.word_timestamps = bool(values["word_timestamps"])
        if values.get("vad") is not None:
            if values["vad"] and not self.vad_available:
                raise ValueError("voice activity detection is disabled on this server")
            self.vad = bool(values["vad"])
        if values.get("vad_threshold") is not None:
            threshold = float(values["vad_threshold"])
            if not MIN_VAD_THRESHOLD <= threshold <= MAX_VAD_THRESHOLD:
                raise ValueError(
                    f"vad_threshold must be {MIN_VAD_THRESHOLD}-{MAX_VAD_THRESHOLD}"
                )
            self.vad_threshold = threshold

    def to_json(self) -> dict[str, Any]:
        return {"sample_rate": self.sample_rate, "language": self.language,
                "endpointing_ms": self.endpointing_ms,
                "word_timestamps": self.word_timestamps, "speaker_diarization": False,
                "vad": self.vad and self.vad_available, "vad_threshold": self.vad_threshold}


@dataclass
class RealtimeSession:
    """Turns a stream of PCM16 frames into delta and completed events."""

    transcribe: Transcribe
    config: SessionConfig = field(default_factory=SessionConfig)
    # Rates frames for speech; None (or `config.vad` off) runs without a VAD.
    vad: ChunkVad | None = None
    partial_interval_s: float = 0.4
    idle_interval_s: float = 2.0
    max_utterance_s: float = 25.0
    keep_s: float = 0.5
    # Audio of the utterance in progress (PCM16 at the session rate).
    _pcm: bytearray = field(default_factory=bytearray)
    # Session time of the buffer's first sample.
    _offset_s: float = 0.0
    _received: int = 0
    _since_run: int = 0
    _sent: list[str] = field(default_factory=list)
    _partials_ok: bool = True
    _item: int = 0
    _stream: VadStream | None = None
    # Session time up to which the model has run, and whether that run left
    # words of an unfinished utterance in the buffer.
    _ran_to_s: float = 0.0
    _words_pending: bool = False

    @property
    def audio_processed(self) -> float:
        return round(self._received / self.config.sample_rate, 3)

    @property
    def _buffer_s(self) -> float:
        return len(self._pcm) / 2 / self.config.sample_rate

    @property
    def _uses_vad(self) -> bool:
        return self.vad is not None and self.config.vad and self.config.vad_available

    async def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        """Adds PCM16 audio; returns the events it produced (may be none)."""

        if len(chunk) % 2:
            raise ValueError("PCM16 frames must have an even number of bytes")
        self._pcm.extend(chunk)
        samples = len(chunk) // 2
        self._received += samples
        self._since_run += samples
        events = self._rate_speech(chunk) if self._uses_vad else []
        interval = self.partial_interval_s
        if (self._stream is not None and not self._words_pending
                and not self._stream.speech_after(self._offset_s)):
            interval = self.idle_interval_s
        if (self._since_run / self.config.sample_rate < interval
                and not self._vad_endpoint_due()):
            return events
        self._since_run = 0
        return [*events, *await self._step(final=False)]

    async def commit(self) -> list[dict[str, Any]]:
        """Finalizes the audio received so far; ends with `input_audio_buffer.committed`."""

        events: list[dict[str, Any]] = []
        if self._stream is not None and self._stream.tracker.speaking:
            self._stream.tracker.close()
            events.append(self._edge_event("stopped", self._stream.tracker.regions[-1].end))
        events += await self._step(final=True)
        return [*events, {"type": "input_audio_buffer.committed",
                          "audio_processed": self.audio_processed}]

    def _rate_speech(self, chunk: bytes) -> list[dict[str, Any]]:
        if self._stream is None:
            assert self.vad is not None
            self._stream = VadStream(self.vad, self.config.sample_rate, self.config.vad_threshold)
        edges = self._stream.push(pcm16_to_float(chunk))
        return [self._edge_event(edge["event"], edge["at"]) for edge in edges]

    def _edge_event(self, edge: str, at: float) -> dict[str, Any]:
        key = "audio_start_ms" if edge == "started" else "audio_end_ms"
        return {"type": f"input_audio_buffer.speech_{edge}", key: round(at * 1000),
                "item_id": self._item_id()}

    def _vad_endpoint_due(self) -> bool:
        """Whether the VAD heard a full endpointing pause the model has not seen yet."""

        stream = self._stream
        if stream is None or stream.tracker.speaking or not stream.tracker.regions:
            return False
        due = stream.tracker.regions[-1].end + self.config.endpointing_ms / 1000
        return self._ran_to_s < due <= stream.rated_s

    async def _run(self) -> Transcription:
        audio = resample(pcm16_to_float(bytes(self._pcm)), self.config.sample_rate)
        return await self.transcribe(audio)

    async def _step(self, *, final: bool) -> list[dict[str, Any]]:
        if not self._pcm:
            return []
        result = await self._run()
        words = list(result.words)
        buffer_s = self._buffer_s
        self._ran_to_s = self._offset_s + buffer_s
        self._words_pending = False
        if final:
            events = [self._completed(words)] if words else []
            self._drop(buffer_s)
            return events
        if not words:
            if buffer_s > self.keep_s * 2:
                self._drop(buffer_s - self.keep_s)
            return []
        silence_s = buffer_s - words[-1].end
        if silence_s * 1000 >= self.config.endpointing_ms:
            event = self._completed(words)
            # CTC marks a word at one frame, so its sound can run past `end`:
            # cut in the middle of the pause, not right after the last word.
            self._drop(words[-1].end + silence_s / 2)
            return [event]
        if buffer_s >= self.max_utterance_s:
            cut = _largest_pause(words)
            done = [word for word in words if word.end <= cut]
            if not done:
                cut, done = words[-1].end, words
            event = self._completed(done)
            self._drop(cut)
            self._words_pending = len(done) < len(words)
            return [event]
        self._words_pending = True
        return self._partial(words[:-1])

    def _partial(self, stable: list[Word]) -> list[dict[str, Any]]:
        if not self._partials_ok:
            return []
        texts = [word.word for word in stable]
        if texts[:len(self._sent)] != self._sent:
            self._partials_ok = False
            return []
        new = texts[len(self._sent):]
        if not new:
            return []
        delta = (" " if self._sent else "") + " ".join(new)
        self._sent = texts
        return [{"type": EVENT_PREFIX + "delta", "item_id": self._item_id(),
                 "delta": delta, "audio_processed": self.audio_processed}]

    def _completed(self, words: list[Word]) -> dict[str, Any]:
        timed = [word.shifted(self._offset_s) for word in words]
        event: dict[str, Any] = {
            "type": EVENT_PREFIX + "completed", "item_id": self._item_id(),
            "transcript": " ".join(word.word for word in timed),
            "language": self.config.language, "audio_processed": self.audio_processed,
        }
        if self.config.word_timestamps:
            event["words"] = [word.to_json() for word in timed]
        self._item += 1
        self._sent, self._partials_ok = [], True
        return event

    def _item_id(self) -> str:
        return f"item_{self._item:04d}"

    def _drop(self, seconds: float) -> None:
        """Removes the first `seconds` of the buffer and advances its offset."""

        samples = min(len(self._pcm) // 2, max(0, round(seconds * self.config.sample_rate)))
        del self._pcm[:samples * 2]
        self._offset_s += samples / self.config.sample_rate
        if self._stream is not None:
            self._stream.forget_before(self._offset_s - 1.0)


def _largest_pause(words: list[Word]) -> float:
    """Cut time in the middle of the longest gap between words in the second half.

    Falls back to the start of the last word, which stays for the next utterance.
    """

    half = words[-1].end / 2
    gaps = [
        (following.start - word.end, (word.end + following.start) / 2)
        for word, following in zip(words, words[1:])
        if word.end >= half
    ]
    if gaps:
        return max(gaps)[1]
    return words[-1].start if len(words) > 1 else words[-1].end
