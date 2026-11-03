"""Audio decoding, resampling and splitting shared by the HTTP and realtime paths."""

from __future__ import annotations

import io
from math import gcd

import numpy as np

MODEL_RATE = 16_000
# The model is trained on clips up to 30 s; longer audio is cut into pieces.
MAX_PIECE_S = 30.0
# A long recording is cut at its quietest 20 ms frame in this part of a piece.
MIN_PIECE_S = 20.0
CUT_FRAME_S = 0.02


def resample(audio: np.ndarray, rate: int) -> np.ndarray:
    """Converts mono float audio from `rate` Hz to 16 kHz float32.

    Raises:
        ValueError: The sample rate is not positive.
    """

    if rate <= 0:
        raise ValueError("invalid sample rate")
    if rate != MODEL_RATE:
        from scipy.signal import resample_poly

        divisor = gcd(rate, MODEL_RATE)
        audio = resample_poly(audio, MODEL_RATE // divisor, rate // divisor)
    return np.asarray(audio, dtype=np.float32)


def decode_file(data: bytes) -> np.ndarray:
    """Decodes an audio file (WAV, FLAC, OGG, ...) to 16 kHz mono float32.

    Stereo is averaged to mono.

    Raises:
        ValueError: The bytes are not a readable audio file.
    """

    import soundfile as sf

    try:
        audio, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except (RuntimeError, sf.LibsndfileError) as exc:
        raise ValueError(f"unreadable audio file: {exc}") from exc
    return resample(audio.mean(axis=1), int(rate))


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    """Little-endian PCM16 bytes as float32 samples in [-1, 1)."""

    usable = len(pcm) - len(pcm) % 2
    return np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0


def split_long(audio: np.ndarray) -> list[tuple[int, np.ndarray]]:
    """Cuts 16 kHz audio into pieces of at most 30 s, each with its start sample.

    A piece longer than 30 s is cut at the quietest 20 ms frame between 20 s
    and 30 s, so cuts fall in pauses where the audio has any.
    """

    limit = int(MAX_PIECE_S * MODEL_RATE)
    earliest = int(MIN_PIECE_S * MODEL_RATE)
    frame = int(CUT_FRAME_S * MODEL_RATE)
    pieces: list[tuple[int, np.ndarray]] = []
    start = 0
    while len(audio) - start > limit:
        window = audio[start + earliest:start + limit]
        frames = len(window) // frame
        energy = (window[:frames * frame].reshape(frames, frame) ** 2).mean(axis=1)
        cut = start + earliest + int(np.argmin(energy)) * frame + frame // 2
        pieces.append((start, audio[start:cut]))
        start = cut
    pieces.append((start, audio[start:]))
    return pieces
