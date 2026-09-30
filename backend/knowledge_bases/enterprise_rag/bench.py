"""Benchmark the enterprise_rag knowledge base on EnterpriseRAG-Bench questions.

    # Retrieval only (embeddings, no LLM): document recall of search_knowledge
    uv run python -m backend.knowledge_bases.enterprise_rag.bench retrieval --k 10

    # Full agent: writes answers.jsonl in the EnterpriseRAG-Bench format
    # {"question_id", "answer", "document_ids"} and resumes where it stopped
    uv run python -m backend.knowledge_bases.enterprise_rag.bench answers --limit 20

    # Document recall of the cited sources in an answers file
    uv run python -m backend.knowledge_bases.enterprise_rag.bench report

Document ids come from the doc_id metadata of each record's source document.
Recall counts only questions that expect documents (not info_not_found or the
ones with no gold document).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv

from backend.knowledge_bases.base import PROJECT_ROOT
from backend.knowledge_bases.enterprise_rag import QUESTIONS_FILE, SPEC
from backend.src.agents.agent import KnowledgeAgentError, KnowledgeAgentService
from backend.src.storage import KnowledgeStore, StorageError


ANSWERS_FILE = QUESTIONS_FILE.with_name("answers.jsonl")
DOC_ID_KEY = SPEC.dataset.id_column if SPEC.dataset else "doc_id"


def load_questions(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_answers(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        answers = (json.loads(line) for line in handle if line.strip())
        return {answer["question_id"]: answer for answer in answers}


def doc_recall(expected: Iterable[str], found: Iterable[str]) -> float:
    expected = set(expected)
    return len(expected & set(found)) / len(expected) if expected else 0.0


def summarize(rows: Sequence[tuple[str, float]]) -> dict[str, tuple[int, float]]:
    """(question_type, recall) rows -> {type: (count, mean recall)}, plus "all"."""

    groups: dict[str, list[float]] = defaultdict(list)
    for question_type, recall in rows:
        groups[question_type].append(recall)
        groups["all"].append(recall)
    return {
        name: (len(values), sum(values) / len(values))
        for name, values in sorted(groups.items(), key=lambda item: (item[0] == "all", item[0]))
    }


def print_summary(title: str, rows: Sequence[tuple[str, float]]) -> None:
    print(f"\n{title}")
    for name, (count, recall) in summarize(rows).items():
        print(f"  {name:<26} n={count:<4} recall={recall:.3f}")


class DocIdResolver:
    """Map evidence ids (stored source documents) to benchmark doc ids."""

    def __init__(self, store: KnowledgeStore) -> None:
        self._store = store
        self._cache: dict[UUID, str | None] = {}

    async def doc_id(self, evidence_id: UUID | str | None) -> str | None:
        if evidence_id is None:
            return None
        evidence_id = UUID(str(evidence_id))
        if evidence_id not in self._cache:
            conversation = await self._store.get_conversation(evidence_id)
            self._cache[evidence_id] = (
                conversation.metadata.get(DOC_ID_KEY) if conversation else None
            )
        return self._cache[evidence_id]

    async def doc_ids(self, evidence_ids: Iterable[UUID | str | None]) -> list[str]:
        """Unique doc ids in first-seen order."""

        found: dict[str, None] = {}
        for evidence_id in evidence_ids:
            if doc_id := await self.doc_id(evidence_id):
                found[doc_id] = None
        return list(found)


def _with_gold(questions: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [question for question in questions if question.get("expected_doc_ids")]


async def run_retrieval(args: argparse.Namespace) -> int:
    questions = _with_gold(load_questions(args.questions))[: args.limit]
    store = await KnowledgeStore.for_kb(SPEC)
    try:
        resolver = DocIdResolver(store)
        semaphore = asyncio.Semaphore(args.concurrency)

        async def evaluate(question: Mapping[str, Any]) -> tuple[str, float]:
            async with semaphore:
                hits = await store.search_knowledge(question["question"], k=args.k)
            found = await resolver.doc_ids(hit.record.evidence_id for hit in hits)
            return question["question_type"], doc_recall(question["expected_doc_ids"], found)

        rows = await asyncio.gather(*(evaluate(question) for question in questions))
    finally:
        await store.aclose()
    print_summary(f"Document recall@{args.k} records of search_knowledge", rows)
    return 0


async def run_answers(args: argparse.Namespace) -> int:
    answered = load_answers(args.output)
    questions = [
        question
        for question in load_questions(args.questions)
        if question["question_id"] not in answered
    ][: args.limit]
    print(f"{len(answered)} already answered in {args.output}; answering {len(questions)}")
    if not questions:
        return 0

    store = await KnowledgeStore.for_kb(SPEC)
    failed = 0
    started = time.perf_counter()
    try:
        agent = KnowledgeAgentService.from_kb(SPEC, store)
        resolver = DocIdResolver(store)
        semaphore = asyncio.Semaphore(args.concurrency)
        args.output.parent.mkdir(parents=True, exist_ok=True)

        with args.output.open("a", encoding="utf-8") as output:

            async def answer(question: Mapping[str, Any]) -> None:
                nonlocal failed
                async with semaphore:
                    try:
                        result = await agent.ask(question["question"])
                    except KnowledgeAgentError as exc:
                        failed += 1
                        print(f"{question['question_id']}: FAILED {exc}", file=sys.stderr)
                        return
                document_ids = await resolver.doc_ids(
                    citation.evidence_id for citation in result.citations
                )
                # One complete line per answer, so an interrupted run resumes cleanly.
                output.write(
                    json.dumps(
                        {
                            "question_id": question["question_id"],
                            "answer": result.answer,
                            "document_ids": document_ids,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                output.flush()
                print(f"{question['question_id']}: {len(document_ids)} documents cited")

            await asyncio.gather(*(answer(question) for question in questions))
    finally:
        await store.aclose()

    print(
        f"Answered {len(questions) - failed}, failed {failed} "
        f"in {time.perf_counter() - started:.1f}s -> {args.output}"
    )
    report(args.questions, args.output)
    return 1 if failed else 0


def report(questions_file: Path, answers_file: Path) -> int:
    answers = load_answers(answers_file)
    rows = [
        (
            question["question_type"],
            doc_recall(
                question["expected_doc_ids"],
                answers[question["question_id"]].get("document_ids") or (),
            ),
        )
        for question in _with_gold(load_questions(questions_file))
        if question["question_id"] in answers
    ]
    if not rows:
        print(f"No answers to report in {answers_file}")
        return 0
    print_summary(f"Document recall of cited sources ({answers_file.name})", rows)
    return 0


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path, default=QUESTIONS_FILE)
    commands = parser.add_subparsers(dest="command", required=True)

    retrieval = commands.add_parser("retrieval", help="Document recall of search_knowledge")
    retrieval.add_argument("--k", type=_positive_int, default=10, help="Records per search (max 20)")
    retrieval.add_argument("--limit", type=_positive_int)
    retrieval.add_argument("--concurrency", type=_positive_int, default=4)

    answers = commands.add_parser("answers", help="Answer questions with the agent")
    answers.add_argument("--output", type=Path, default=ANSWERS_FILE)
    answers.add_argument("--limit", type=_positive_int, help="Answer at most this many new questions")
    answers.add_argument("--concurrency", type=_positive_int, default=2)

    report_parser = commands.add_parser("report", help="Document recall of an answers file")
    report_parser.add_argument("--output", type=Path, default=ANSWERS_FILE)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.WARNING)
    if not args.questions.exists():
        print(
            f"Error: {args.questions} not found; run "
            "`uv run python -m backend.knowledge_bases.enterprise_rag.prepare`",
            file=sys.stderr,
        )
        return 2
    try:
        if args.command == "retrieval":
            return asyncio.run(run_retrieval(args))
        if args.command == "answers":
            return asyncio.run(run_answers(args))
        return report(args.questions, args.output)
    except (OSError, ValueError, StorageError, KnowledgeAgentError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
