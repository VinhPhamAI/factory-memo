"""VieGem-ASR inference: model loading, batched GPU decoding and CTC word timings.

Requests from the HTTP and realtime paths go through one queue. A single
worker task gathers up to `max_batch` clips (waiting at most `batch_wait_s`
for more), runs them as one padded batch in a worker thread and resolves each
request's future with its own `Transcription`.

Words and times come from the greedy CTC path. The encoder emits one frame per
40 ms; a unit is placed on the frames of its non-blank run, and a Gemma piece
starting with "▁" starts a new word. Times are therefore approximate (one
frame) and words are lowercase without punctuation, as the model writes them.

On near-silent audio the model tends to emit a word at the very last frame of
its input (seen down to -70 dBFS and on digital silence). Every clip therefore
gets `END_PAD_S` of trailing silence, and words starting in that padding are
dropped; this does not change accuracy on FLEURS.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .audio import MODEL_RATE

logger = logging.getLogger(__name__)

FRAME_S = 0.04
# Clips shorter than 0.1 s are not sent to the model and return no words.
MIN_SAMPLES = MODEL_RATE // 10
MAX_SAMPLES = 30 * MODEL_RATE
END_PAD_S = 0.4
CHECKPOINT = "viegem_asr.pt"


@dataclass(frozen=True)
class Word:
    word: str
    start: float
    end: float
    confidence: float

    def shifted(self, seconds: float) -> Word:
        """The word moved `seconds` later on the timeline."""

        return Word(self.word, round(self.start + seconds, 3), round(self.end + seconds, 3),
                    self.confidence)

    def to_json(self) -> dict[str, float | str]:
        return {"word": self.word, "start": self.start, "end": self.end,
                "confidence": self.confidence}


@dataclass(frozen=True)
class Transcription:
    text: str
    words: tuple[Word, ...] = field(default=())


def ctc_words(
    best: np.ndarray, confidence: np.ndarray, pieces: Sequence[str],
    decode: Callable[[list[int]], str], frame_s: float = FRAME_S,
) -> Transcription:
    """Words with times from one greedy CTC path.

    Args:
        best: Most likely unit per valid frame (0 = blank, 1 = unk, 2.. = pieces).
        confidence: Probability of that unit per frame.
        pieces: Tokenizer piece of each unit from 2 on (`pieces[u - 2]`).
        decode: Turns a list of units into text, as `Units.decode` does.
        frame_s: Seconds per encoder frame.

    Returns:
        The decoded text and its words; a word spans the frames of its units'
        runs, and its confidence is the mean probability over those frames.
    """

    runs: list[tuple[int, int, int]] = []  # unit, first frame, last frame
    previous = -1
    for index, unit in enumerate(best.tolist()):
        if unit != previous and unit >= 2:
            runs.append((unit, index, index))
        elif unit == previous and unit >= 2:
            runs[-1] = (unit, runs[-1][1], index)
        previous = unit
    groups: list[list[tuple[int, int, int]]] = []
    for run in runs:
        if not groups or pieces[run[0] - 2].startswith("▁"):
            groups.append([run])
        else:
            groups[-1].append(run)
    words: list[Word] = []
    for group in groups:
        text = decode([unit for unit, _, _ in group]).strip()
        if not text:
            continue
        frames = np.concatenate([confidence[first:last + 1] for _, first, last in group])
        words.append(Word(
            text,
            round(group[0][1] * frame_s, 3),
            round((group[-1][2] + 1) * frame_s, 3),
            round(float(frames.mean()), 4),
        ))
    return Transcription(" ".join(word.word for word in words), tuple(words))


def before(result: Transcription, end_s: float) -> Transcription:
    """The transcription without words that start at or after `end_s`."""

    words = tuple(word for word in result.words if word.start < end_s)
    if len(words) == len(result.words):
        return result
    return Transcription(" ".join(word.word for word in words), words)


class Engine:
    """Loads VieGem-ASR once and transcribes 16 kHz clips of up to 30 s in batches."""

    def __init__(
        self, model_dir: str | Path, encoder_dir: str | Path, *, device: str = "cuda",
        max_batch: int = 8, batch_wait_s: float = 0.005, queue_size: int = 256,
        compile_model: bool = False,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.encoder_dir = Path(encoder_dir)
        self.device = device
        self.max_batch = max_batch
        self.batch_wait_s = batch_wait_s
        self.queue_size = queue_size
        self.compile_model = compile_model
        self.ready = False
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None

    # ------------------------------------------------------------- loading

    def load(self) -> None:
        """Builds the model from the checkpoint and the local encoder copy.

        The checkpoint holds the fine-tuned weights; the frozen encoder layers
        come from EmbeddingGemma 2. Loading checks that every weight outside
        the encoder is in the checkpoint, then runs a warm-up clip and checks
        the 40 ms frame rate the word timings assume.

        Raises:
            FileNotFoundError: The checkpoint or the encoder folder is missing.
            RuntimeError: The checkpoint does not match the model code.
        """

        import torch
        from transformers import AutoTokenizer

        checkpoint = self.model_dir / CHECKPOINT
        for required in (checkpoint, self.model_dir / "asr" / "model.py",
                         self.encoder_dir / "config.json"):
            if not required.exists():
                raise FileNotFoundError(f"missing {required}")
        # The model code ships with the checkpoint (asr/ in the model repository).
        if str(self.model_dir) not in sys.path:
            sys.path.insert(0, str(self.model_dir))
        from asr.model import CTCModel, ModelConfig, greedy_decode  # noqa: F401
        from asr.text import Units

        # Plain tensors, dicts and lists only; refuse pickled code.
        ck = torch.load(checkpoint, map_location="cpu", weights_only=True)
        cfg = ModelConfig.from_dict(ck["model_config"])
        self.source_encoder_id = cfg.encoder_id
        cfg.encoder_id = str(self.encoder_dir)
        self.units = Units(ck["gemma_ids"], model_id=str(self.encoder_dir))
        self.tokenizer = AutoTokenizer.from_pretrained(self.encoder_dir)
        self.pieces = self.tokenizer.convert_ids_to_tokens(self.units.gemma_ids)

        model = CTCModel(cfg, self.units.gemma_ids)
        result = model.load_state_dict(ck["model"], strict=False)
        missing = [key for key in result.missing_keys if not key.startswith("encoder.")]
        if missing or result.unexpected_keys:
            raise RuntimeError(
                f"checkpoint does not match the model: missing {missing[:5]}, "
                f"unexpected {result.unexpected_keys[:5]}"
            )
        self.model = model.to(self.device).eval()
        if self.compile_model:
            self.model = torch.compile(self.model, mode="reduce-overhead")
        self._torch = torch

        warmup = np.random.default_rng(0).normal(0, 0.01, 2 * MODEL_RATE).astype(np.float32)
        frames = self._forward([warmup])[1][0]
        if abs(frames * FRAME_S - 2.0) > 2 * FRAME_S:
            raise RuntimeError(f"unexpected frame rate: {frames} frames for 2 s")
        self.ready = True
        logger.info("VieGem-ASR loaded on %s (%d units)", self.device, len(self.units))

    # ----------------------------------------------------------- inference

    def _forward(self, clips: Sequence[np.ndarray]):
        torch = self._torch
        lengths = torch.tensor([len(clip) for clip in clips], dtype=torch.long)
        batch = torch.zeros((len(clips), int(lengths.max())), dtype=torch.float32)
        for index, clip in enumerate(clips):
            batch[index, :len(clip)] = torch.from_numpy(clip)
        with torch.inference_mode(), torch.autocast(
            "cuda", dtype=torch.bfloat16, enabled=self.device.startswith("cuda")
        ):
            logits, out_lens = self.model(
                batch.to(self.device), lengths.to(self.device), streaming=False
            )
        probs = torch.softmax(logits.float(), dim=-1)
        confidence, best = probs.max(dim=-1)
        return best.cpu().numpy(), out_lens.cpu().tolist(), confidence.cpu().numpy()

    def infer_batch(self, clips: Sequence[np.ndarray]) -> list[Transcription]:
        """Transcribes 16 kHz float32 clips of 0.1-30 s in one forward pass.

        Each clip is padded with `END_PAD_S` of silence; words found in the
        padding are dropped (see the module note).
        """

        pad = np.zeros(int(END_PAD_S * MODEL_RATE), dtype=np.float32)
        best, lengths, confidence = self._forward([np.concatenate([c, pad]) for c in clips])
        decode = lambda units: self.units.decode(self.tokenizer, units)  # noqa: E731
        return [
            before(
                ctc_words(best[i, :lengths[i]], confidence[i, :lengths[i]], self.pieces, decode),
                len(clips[i]) / MODEL_RATE,
            )
            for i in range(len(clips))
        ]

    async def transcribe(self, clip: np.ndarray) -> Transcription:
        """Queues one 16 kHz clip (at most 30 s) and waits for its transcription.

        Raises:
            ValueError: The clip is longer than 30 s.
            RuntimeError: The engine is not started.
        """

        if len(clip) > MAX_SAMPLES:
            raise ValueError("a clip must be at most 30 s")
        if len(clip) < MIN_SAMPLES:
            return Transcription("")
        if self._queue is None:
            raise RuntimeError("the engine is not started")
        future = asyncio.get_running_loop().create_future()
        await self._queue.put((clip, future))
        return await future

    async def start(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.queue_size)
        self._worker = asyncio.create_task(self._batches())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker

    async def _batches(self) -> None:
        loop = asyncio.get_running_loop()
        queue = self._queue
        assert queue is not None
        while True:
            batch = [await queue.get()]
            deadline = loop.time() + self.batch_wait_s
            while len(batch) < self.max_batch:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(queue.get(), remaining))
                except TimeoutError:
                    break
            pending = [(clip, future) for clip, future in batch if not future.done()]
            try:
                if pending:
                    results = await asyncio.to_thread(
                        self.infer_batch, [clip for clip, _ in pending]
                    )
                    for (_, future), result in zip(pending, results):
                        if not future.done():
                            future.set_result(result)
            except Exception as exc:
                logger.exception("Batch of %d clips failed", len(pending))
                for _, future in pending:
                    if not future.done():
                        future.set_exception(exc)
            finally:
                for _ in batch:
                    queue.task_done()
