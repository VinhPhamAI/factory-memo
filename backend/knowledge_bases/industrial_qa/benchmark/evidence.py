"""Benchmark evidence items (conversation turns, manual chunks) of retrieved sources."""
from __future__ import annotations

import json
import re
from typing import Any

from backend.src.storage import KnowledgeRecord


# Turns a lesson maps to when it quotes none (see record_turns).
LESSON_TURNS = 2
RETRIEVED_LIMIT = 20
_WORD_RE = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")
_STOP_WORDS = frozenset(
    "a an and are as at be before but by can do for from has have if in is it "
    "its not of on or so that the then this to was we when with you your".split()
)


def tokens(text: str) -> set[str]:
    return {word for word in _WORD_RE.findall(text.lower()) if word not in _STOP_WORDS}


def _by_overlap(text: str, turns: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """(shared words, turn id) of every turn, most shared first, then by turn."""

    words = tokens(text)
    return sorted(
        ((len(words & tokens(turn["text"])), turn["turn_id"]) for turn in turns),
        key=lambda item: (-item[0], item[1]),
    )


def record_turns(
    record: KnowledgeRecord,
    turns: list[dict[str, Any]],
    question: str = "",
    *,
    limit: int = LESSON_TURNS,
) -> list[int]:
    """Conversation turns a record points to, for turn-level retrieval scoring.

    `turns` are the candidates (the expert turns, see data.expert_turns). A Case
    Card names the turns of its evidence quotes, a lesson its source_turns;
    they are ranked by the words they share with the question (a card quotes
    the symptoms before the expert's answer). A record naming no turn (one of
    an older extraction) maps to the `limit` turns sharing the most words with
    its claim.
    """

    evidence = (record.context.get("case_card") or {}).get("evidence") or []
    named = [item.get("turn_id") for item in evidence] + list(record.context.get("source_turns") or [])
    cited = {
        int(match.group(1))
        for label in named if (match := re.fullmatch(r"T0*(\d+)", str(label or "")))
    }
    cited_turns = [turn for turn in turns if turn["turn_id"] in cited]
    if cited_turns:
        return [turn_id for _, turn_id in _by_overlap(question, cited_turns)]
    ranked = _by_overlap(f"{record.subject} {record.claim}", turns)
    return [turn_id for overlap, turn_id in ranked[:limit] if overlap]


def covered_facts(
    records: list[KnowledgeRecord], turns: list[dict[str, Any]], fact_turns: dict[str, list[int]]
) -> list[str]:
    """Expert facts stated in a turn that some record points to (see record_turns)."""

    pointed = {turn for record in records for turn in record_turns(record, turns)}
    return [fact_id for fact_id, fact in fact_turns.items() if pointed & set(fact)]


def concatenate(
    rankings: list[list[dict[str, Any]]], limit: int | None = RETRIEVED_LIMIT
) -> list[dict[str, Any]]:
    """Items of each ranking in turn, dropping repeats."""

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (item for ranking in rankings for item in ranking):
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged[:limit]


def cited_ranking(
    sources: dict[str, dict[str, Any]], cited: list[str], uncited: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Items of the cited sources in citation order, then the uncited ranking.

    The answer model has read every source, so its citations rerank them.
    """

    return concatenate([*(sources[label]["items"] for label in cited), uncited])
