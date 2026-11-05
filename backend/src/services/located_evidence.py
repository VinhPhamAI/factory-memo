"""Map extracted quotes and document passages to stored conversion segments."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from backend.src.agents.rag import Chunk
from backend.src.services.converters import Segment
from backend.src.storage.models import KnowledgeRecord


def segments_from_metadata(metadata: Mapping[str, Any]) -> list[Segment]:
    """Load validated conversion segments from source metadata.

    Invalid or stale entries are ignored so older sources keep their whole-source
    fallback instead of receiving an invented location.
    """

    conversion = metadata.get("conversion")
    values = conversion.get("segments") if isinstance(conversion, Mapping) else None
    if not isinstance(values, list):
        return []
    segments: list[Segment] = []
    for value in values:
        if not isinstance(value, Mapping) or not isinstance(
            value.get("location"), Mapping
        ):
            continue
        try:
            start, end = int(value["start"]), int(value["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        segments.append(Segment(start, end, dict(value["location"])))
    return segments


def references_for_record(
    record: KnowledgeRecord,
    text: str,
    segments: Sequence[Segment],
) -> list[dict[str, Any]]:
    """Locate every exact evidence quote of a knowledge record.

    Missing quotes become explicit source fallbacks. A record with no quote keeps
    the ordinary whole-source reference.
    """

    quotes = _evidence_quotes(record)
    if not quotes:
        return [{"type": "source"}]
    references: list[dict[str, Any]] = []
    for quote in quotes:
        start = text.find(quote)
        if start < 0:
            references.append({
                "type": "source", "quote": quote, "location_missing": True,
            })
            continue
        located = references_for_span(text, start, start + len(quote), segments)
        if located:
            references.extend(located)
        else:
            references.append({"type": "source", "quote": quote})
    return _deduplicate(references)


def references_for_chunks(
    chunks: Sequence[Chunk],
    text: str,
    segments: Sequence[Segment],
) -> list[list[dict[str, Any]]]:
    """Map document passages to every conversion segment their source span crosses."""

    references: list[list[dict[str, Any]]] = []
    cursor = 0
    normalized, offsets = _normalized_with_offsets(text)
    for chunk in chunks:
        target = _normalize(chunk.text)
        index = normalized.find(target, cursor) if target else -1
        if index < 0:
            references.append([{"type": "passage", "chunk_index": chunk.index}])
            continue
        # Chunks may overlap; the next span can start inside this one but must
        # not move backwards to an earlier repeated passage.
        cursor = index + 1
        start = offsets[index]
        end = offsets[index + len(target) - 1] + 1
        located = references_for_span(text, start, end, segments)
        references.append(
            located or [{"type": "passage", "chunk_index": chunk.index}]
        )
    return references


def references_for_span(
    text: str,
    start: int,
    end: int,
    segments: Sequence[Segment],
) -> list[dict[str, Any]]:
    """Return exact stored locations overlapping a character span."""

    references: list[dict[str, Any]] = []
    for segment in segments:
        overlap_start = max(start, segment.start)
        overlap_end = min(end, segment.end)
        if overlap_start >= overlap_end:
            continue
        reference = dict(segment.location)
        quote = text[overlap_start:overlap_end]
        if quote and reference.get("type") != "image":
            reference["quote"] = quote
        references.append(reference)
    return _deduplicate(references)


def _evidence_quotes(record: KnowledgeRecord) -> list[str]:
    """Read the canonical quote list used by verification and conflict checks."""

    card = record.context.get("case_card")
    values = (
        card.get("evidence")
        if isinstance(card, Mapping)
        else record.context.get("quotes")
    )
    if not isinstance(values, list):
        return []
    quotes = [
        str(
            value.get("quote") or ""
            if isinstance(value, Mapping)
            else value or ""
        ).strip()
        for value in values
    ]
    return [quote for quote in quotes if quote]


def _normalize(value: str) -> str:
    """Match the chunker's whitespace joining without changing source offsets."""

    return re.sub(r"\s+", " ", value.strip())


def _normalized_with_offsets(value: str) -> tuple[str, list[int]]:
    """Normalize whitespace and retain the original offset of every output character."""

    output: list[str] = []
    offsets: list[int] = []
    pending_space: int | None = None
    for index, character in enumerate(value):
        if character.isspace():
            if output:
                pending_space = index if pending_space is None else pending_space
            continue
        if pending_space is not None and output:
            output.append(" ")
            offsets.append(pending_space)
        pending_space = None
        output.append(character)
        offsets.append(index)
    return "".join(output), offsets


def _deduplicate(references: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep reference order while removing structurally identical entries."""

    seen: set[str] = set()
    result = []
    for reference in references:
        key = json.dumps(reference, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(reference)
    return result
