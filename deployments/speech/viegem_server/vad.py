"""Voice activity detection for realtime sessions with Silero VAD.

Silero VAD 6 (MIT license; the ONNX model ships inside the pinned
`silero-vad` package) rates every 32 ms chunk of 16 kHz audio with the
probability that it contains speech. ONNX Runtime runs it on one CPU thread in
about 0.15 ms per chunk. The model keeps a recurrent state, passed in and out
explicitly, so each stream is rated with its own state.

Probabilities become speech regions with hysteresis, as in Silero's
`get_speech_timestamps`: speech starts at a chunk rated at least `threshold`
and ends once chunks stay below `threshold - 0.15` for `MIN_SILENCE_S`.

The state is reset after `RESET_AFTER_S` without speech. Without the reset,
the model adapts to a loud speaker and then misses a much quieter one: on
FLEURS clips joined with random gains of 0 to -20 dB, it found 89.9% of the
words; with the reset, 97.2%. Sessions therefore never drop audio or words on
the VAD's word alone (see `realtime.py`).
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from math import ceil, gcd
from pathlib import Path
from typing import Any

import numpy as np

from .audio import MODEL_RATE

logger = logging.getLogger(__name__)

CHUNK = 512
CHUNK_S = CHUNK / MODEL_RATE
# Silero prepends the last 64 samples of the previous chunk to each chunk.
CONTEXT = 64
DEFAULT_THRESHOLD = 0.5
NEG_THRESHOLD_GAP = 0.15
MIN_SILENCE_S = 0.3
RESET_AFTER_S = 1.0
SILERO_VERSION = "6.2.3"
_RATE = np.array(MODEL_RATE, dtype=np.int64)


@dataclass(frozen=True)
class Region:
    """A stretch of speech in seconds."""

    start: float
    end: float


class ChunkVad:
    """Rates 32 ms chunks of 16 kHz audio; subclasses provide the model."""

    ready = False

    def initial_state(self, batch: int) -> Any:
        raise NotImplementedError

    def step(self, chunks: np.ndarray, state: Any) -> tuple[np.ndarray, Any]:
        """Speech probability of each row of `chunks` (`CONTEXT + CHUNK` samples)."""

        raise NotImplementedError


def model_path() -> Path:
    """The ONNX model inside the installed `silero-vad` package.

    The package is located without importing it: its `__init__` imports
    PyTorch and sets PyTorch to one thread for the whole process.

    Raises:
        FileNotFoundError: The package or its model file is missing.
    """

    spec = importlib.util.find_spec("silero_vad")
    if spec is None or not spec.submodule_search_locations:
        raise FileNotFoundError("the silero-vad package is not installed")
    path = Path(next(iter(spec.submodule_search_locations))) / "data" / "silero_vad.onnx"
    if not path.is_file():
        raise FileNotFoundError(f"missing {path}")
    return path


class SileroVad(ChunkVad):
    """Silero VAD 6 run with ONNX Runtime on one CPU thread per call."""

    def load(self) -> None:
        """Opens the model and checks that it rates a batch of chunks.

        Raises:
            FileNotFoundError: The model file is missing.
            RuntimeError: The model's inputs or outputs are not as expected.
        """

        import onnxruntime

        onnxruntime.set_default_logger_severity(3)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = onnxruntime.InferenceSession(
            str(model_path()), sess_options=options, providers=["CPUExecutionProvider"]
        )
        names = sorted(i.name for i in self._session.get_inputs())
        if names != ["input", "sr", "state"]:
            raise RuntimeError(f"unexpected Silero VAD inputs {names}")
        probs, _ = self.step(np.zeros((2, CONTEXT + CHUNK), dtype=np.float32),
                             self.initial_state(2))
        if probs.shape != (2,) or not np.all((probs >= 0) & (probs <= 1)):
            raise RuntimeError(f"unexpected Silero VAD output {probs!r}")
        self.ready = True
        logger.info("Silero VAD %s loaded (ONNX Runtime, CPU)", SILERO_VERSION)

    def initial_state(self, batch: int) -> np.ndarray:
        return np.zeros((2, batch, 128), dtype=np.float32)

    def step(self, chunks: np.ndarray, state: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        probs, state = self._session.run(None, {
            "input": np.ascontiguousarray(chunks, dtype=np.float32),
            "state": state, "sr": _RATE,
        })
        return probs[:, 0], state


@dataclass
class RegionTracker:
    """Turns chunk probabilities into speech regions with hysteresis."""

    threshold: float = DEFAULT_THRESHOLD
    min_silence_s: float = MIN_SILENCE_S
    regions: list[Region] = field(default_factory=list)
    start: float | None = None
    # End of the last chunk rated as speech in the region in progress.
    voiced_end: float = 0.0

    @property
    def speaking(self) -> bool:
        return self.start is not None

    def push(self, prob: float, at: float) -> str | None:
        """Adds the chunk starting at `at` seconds.

        Returns:
            "started" or "stopped" when a region starts or ends, else None.
        """

        end = at + CHUNK_S
        if self.start is None:
            if prob >= self.threshold:
                self.start, self.voiced_end = at, end
                return "started"
            return None
        if prob >= self.threshold - NEG_THRESHOLD_GAP:
            self.voiced_end = end
        elif end - self.voiced_end >= self.min_silence_s:
            self.close()
            return "stopped"
        return None

    def close(self) -> None:
        """Ends the region in progress at its last speech chunk."""

        if self.start is not None:
            self.regions.append(Region(round(self.start, 3), round(self.voiced_end, 3)))
            self.start = None


class StreamResampler:
    """Resamples a stream of chunks to 16 kHz as if the audio were one array.

    Each block is resampled with a margin of input on both sides, so chunk
    edges get no filter artifacts; the last margin waits for the next chunk.
    """

    def __init__(self, rate: int) -> None:
        divisor = gcd(rate, MODEL_RATE)
        self.up, self.down = MODEL_RATE // divisor, rate // divisor
        self.margin = self.down * ceil(0.01 * rate / self.down)
        self._buffer = np.zeros(self.margin, dtype=np.float32)

    def push(self, audio: np.ndarray) -> np.ndarray:
        if self.up == self.down:
            return np.asarray(audio, dtype=np.float32)
        from scipy.signal import resample_poly

        self._buffer = np.concatenate([self._buffer, audio.astype(np.float32)])
        usable = (len(self._buffer) - 2 * self.margin) // self.down * self.down
        if usable <= 0:
            return np.zeros(0, dtype=np.float32)
        block = resample_poly(self._buffer[:usable + 2 * self.margin], self.up, self.down)
        lead = self.margin * self.up // self.down
        self._buffer = self._buffer[usable:]
        return block[lead:lead + usable * self.up // self.down].astype(np.float32)


class VadStream:
    """Speech regions of one live stream, in seconds since its first sample."""

    def __init__(self, vad: ChunkVad, rate: int, threshold: float = DEFAULT_THRESHOLD,
                 reset_after_s: float = RESET_AFTER_S) -> None:
        self._vad = vad
        self._resampler = StreamResampler(rate)
        self._state = vad.initial_state(1)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._chunks = 0
        self._quiet_chunks = 0
        self._reset_chunks = max(1, round(reset_after_s / CHUNK_S))
        self.tracker = RegionTracker(threshold)

    @property
    def rated_s(self) -> float:
        """Seconds of audio rated so far."""

        return self._chunks * CHUNK_S

    def push(self, audio: np.ndarray) -> list[dict[str, Any]]:
        """Rates new float audio at the stream rate.

        Returns:
            One `{"event": "started" | "stopped", "at": seconds}` per region
            edge found; a stop is reported at the region's last speech chunk.
        """

        self._pending = np.concatenate([self._pending, self._resampler.push(audio)])
        edges = []
        while len(self._pending) >= CHUNK:
            chunk = self._pending[:CHUNK][None, :]
            self._pending = self._pending[CHUNK:]
            prob, self._state = self._vad.step(np.concatenate([self._context, chunk], axis=1),
                                               self._state)
            self._context = chunk[:, -CONTEXT:]
            at = self.rated_s
            self._chunks += 1
            p = float(prob[0])
            if p < self.tracker.threshold - NEG_THRESHOLD_GAP:
                self._quiet_chunks += 1
                if self._quiet_chunks >= self._reset_chunks:
                    self._state, self._quiet_chunks = self._vad.initial_state(1), 0
            else:
                self._quiet_chunks = 0
            edge = self.tracker.push(p, at)
            if edge == "started":
                edges.append({"event": edge, "at": round(at, 3)})
            elif edge == "stopped":
                edges.append({"event": edge, "at": self.tracker.regions[-1].end})
        return edges

    def speech_after(self, seconds: float) -> bool:
        """Whether any speech was found after `seconds` (or is in progress)."""

        return self.tracker.speaking or any(r.end > seconds for r in self.tracker.regions)

    def forget_before(self, seconds: float) -> None:
        """Drops regions that ended before `seconds`; they are no longer needed."""

        self.tracker.regions = [r for r in self.tracker.regions if r.end >= seconds]
