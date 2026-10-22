"""Benchmark files: questions, conversations and manual chunks, gold and public."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.knowledge_bases.base import PROJECT_ROOT
from backend.src.agents.rag.chunking import Document


BENCHMARK_DIR = PROJECT_ROOT / "benchmark"
PUBLIC_DIR = BENCHMARK_DIR / "public"
GOLD_DIR = BENCHMARK_DIR / "benchmark"
GENERATION_DIR = BENCHMARK_DIR / "generation"
# Speaker roles whose turns carry expert knowledge; records point to these only
# (the others report symptoms or ask).
EXPERT_ROLES = frozenset({"maintenance_engineer", "controls_engineer", "senior_technician"})


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A checkpoint survives interruption without leaving half a JSON document.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def gold_questions(split: str) -> list[dict[str, Any]]:
    return read_jsonl(GOLD_DIR / f"{split}.jsonl")


def gold_conversations() -> dict[str, dict[str, Any]]:
    """Conversations with fact_turns: the turns stating each expert fact."""

    return {c["conversation_id"]: c for c in read_jsonl(GENERATION_DIR / "conversations.jsonl")}


def public_questions(split: str) -> dict[str, dict[str, Any]]:
    return {q["question_id"]: q for q in read_jsonl(PUBLIC_DIR / f"questions_{split}.jsonl")}


def public_conversations() -> dict[str, dict[str, Any]]:
    return {c["conversation_id"]: c for c in read_jsonl(PUBLIC_DIR / "conversations.jsonl")}


def corpus_chunks() -> dict[str, dict[str, Any]]:
    return {c["chunk_id"]: c for c in read_jsonl(PUBLIC_DIR / "corpus_chunks.jsonl")}


def turn_label(turn_id: int) -> str:
    return f"T{turn_id:02d}"


def render_transcript(conversation: dict[str, Any]) -> str:
    """[T01] speaker (role): text lines, so Case Card quotes carry turn ids."""

    roles = {speaker["id"]: speaker["role"] for speaker in conversation["speakers"]}
    return "\n".join(
        f"[{turn_label(turn['turn_id'])}] {turn['speaker']} ({roles.get(turn['speaker'], 'unknown')}): "
        f"{turn['text']}"
        for turn in conversation["turns"]
    )


def expert_turns(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    roles = {speaker["id"]: speaker["role"] for speaker in conversation["speakers"]}
    return [turn for turn in conversation["turns"] if roles.get(turn["speaker"]) in EXPERT_ROLES]


def chunk_document(chunk: dict[str, Any]) -> Document:
    return Document(id=chunk["chunk_id"], title=" > ".join(chunk["section_path"]), text=chunk["text"],
                    metadata={"reference_doc_id": chunk["doc_id"], "page_start": chunk["page_start"],
                              "page_end": chunk["page_end"], "section_path": chunk["section_path"]})
