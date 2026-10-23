"""Near-duplicate signatures of evidence text: MinHash over word shingles, with LSH bands.

The exact content fingerprint (`extraction_runs.content_sha256`) only catches
identical text. Re-recordings, edited copies and excerpts of a source differ
slightly, so their overlap is measured on 5-word shingles:

- Jaccard |A∩B| / |A∪B|: the two texts are nearly the same;
- containment |A∩B| / |A|: the new text A is (mostly) part of the earlier B;
- coverage |A∩B| / |B|: the new text contains (most of) the earlier one.

Comparing every pair of texts does not scale, so each extracted text keeps a
MinHash signature cut into LSH bands. Texts sharing a band are candidates;
their scores are then computed exactly from the texts. Scores decide only a
warning shown to the reviewer: nothing is blocked or merged on similarity, and
embedding similarity is never used for this.

LSH limitation: a candidate is found with high probability when the Jaccard
similarity is at least ~0.2 (the 64 bands of 2 rows). An excerpt of a much
longer source (more than ~5x its size) has a low Jaccard similarity and may
be missed even when its containment is high.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import unicodedata
from dataclasses import dataclass


SHINGLE_WORDS = 5
NUM_PERM = 128
BAND_ROWS = 2
SIGNATURE_VERSION = f"minhash-w{SHINGLE_WORDS}-p{NUM_PERM}-r{BAND_ROWS}-v1"

_PRIME = (1 << 61) - 1
# Fixed seed: stored signatures must stay comparable across processes.
_random = random.Random(20261023)
_PERMUTATIONS = tuple((_random.randrange(1, _PRIME), _random.randrange(0, _PRIME)) for _ in range(NUM_PERM))
_WORD = re.compile(r"\w+")
# `[T01]` turn tags and "Speaker A:" labels differ between recordings of one conversation.
_TURN = re.compile(r"\[T\d+\]\s*(?:(?:Speaker [A-Z]+|Unknown speaker):)?")


def words(text: str) -> list[str]:
    """Lower-cased words; punctuation, spacing and turn labels are ignored."""

    return _WORD.findall(_TURN.sub(" ", unicodedata.normalize("NFC", text)).casefold())


def shingles(text: str, size: int = SHINGLE_WORDS) -> frozenset[str]:
    """Overlapping runs of `size` words; a shorter text is one shingle."""

    tokens = words(text)
    if len(tokens) <= size:
        return frozenset([" ".join(tokens)]) if tokens else frozenset()
    return frozenset(" ".join(tokens[i:i + size]) for i in range(len(tokens) - size + 1))


def _hash(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big") % _PRIME


def minhash(items: frozenset[str]) -> list[int]:
    if not items:
        return []
    hashes = [_hash(item) for item in items]
    return [min((a * x + b) % _PRIME for x in hashes) for a, b in _PERMUTATIONS]


def bands(signature: list[int]) -> list[str]:
    """LSH band keys `<band>:<hash of its rows>`; texts sharing one are candidates."""

    keys = []
    for band, start in enumerate(range(0, len(signature), BAND_ROWS)):
        rows = b"".join(value.to_bytes(8, "big") for value in signature[start:start + BAND_ROWS])
        keys.append(f"{band}:{hashlib.blake2b(rows, digest_size=8).hexdigest()}")
    return keys


@dataclass(frozen=True)
class Signature:
    shingles: frozenset[str]
    bands: list[str]


def signature(text: str) -> Signature:
    items = shingles(text)
    return Signature(items, bands(minhash(items)))


@dataclass(frozen=True)
class Overlap:
    jaccard: float
    containment: float
    coverage: float


def overlap(new: frozenset[str], earlier: frozenset[str]) -> Overlap:
    shared = len(new & earlier)
    if not shared:
        return Overlap(0.0, 0.0, 0.0)
    return Overlap(shared / len(new | earlier), shared / len(new), shared / len(earlier))


NEAR_DUPLICATE = "near_duplicate"
CONTAINED = "contained"
SIMILAR = "similar"
# Levels that need the reviewer's confirmation before extraction.
CONFIRM_LEVELS = (NEAR_DUPLICATE, CONTAINED)


def _threshold(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


@dataclass(frozen=True)
class Thresholds:
    """Jaccard / containment that need confirmation; `info` only informs."""

    jaccard: float = 0.9
    containment: float = 0.9
    info: float = 0.6

    def __post_init__(self):
        # A percentage such as 90 would silently disable the warning.
        for name, value in self.to_json().items():
            if not 0 < value <= 1:
                raise ValueError(f"Near-duplicate threshold {name} must be in (0, 1], got {value}")

    @classmethod
    def from_env(cls) -> "Thresholds":
        return cls(
            jaccard=_threshold("NEAR_DUPLICATE_JACCARD", cls.jaccard),
            containment=_threshold("NEAR_DUPLICATE_CONTAINMENT", cls.containment),
            info=_threshold("NEAR_DUPLICATE_INFO", cls.info),
        )

    def level(self, scores: Overlap) -> str | None:
        if scores.jaccard >= self.jaccard:
            return NEAR_DUPLICATE
        if scores.containment >= self.containment:
            return CONTAINED
        if max(scores.jaccard, scores.containment, scores.coverage) >= self.info:
            return SIMILAR
        return None

    def to_json(self) -> dict[str, float]:
        return {"jaccard": self.jaccard, "containment": self.containment, "info": self.info}
