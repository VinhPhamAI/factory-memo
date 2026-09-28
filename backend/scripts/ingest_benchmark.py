"""Ingest the benchmark conversations, then ask the knowledge agent one question.

Usage:
    uv run python -m backend.scripts.ingest_benchmark "How do I fix the unstable sensor on M01?"
    uv run python -m backend.scripts.ingest_benchmark --skip-ingest "..."
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from pathlib import Path

from dotenv import load_dotenv

from backend.src.agents.agent import KnowledgeAgentService
from backend.src.services.knowledge_extraction import KnowledgeExtractionService
from backend.src.services.knowledge_ingestion import KnowledgeIngestionService
from backend.src.storage import KnowledgeStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXTRACTION_MAX_TOKENS = 8192
DEFAULT_CSV = PROJECT_ROOT / "data" / "expert_knowledge_extraction_benchmark_20_conversations_en.csv"


async def ingest(store: KnowledgeStore, csv_path: Path, limit: int | None) -> None:
    # Headroom for reasoning models, whose thinking tokens count against max_tokens.
    extractor = KnowledgeExtractionService.from_env(max_tokens=EXTRACTION_MAX_TOKENS)
    ingestion = KnowledgeIngestionService(extractor, store)
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows[:limit]:
        case_id = row.get("case_id")
        try:
            result = await ingestion.ingest(
                row["raw_conversation"],
                metadata={"source": csv_path.name, "case_id": case_id, "domain": row.get("domain")},
            )
        except Exception as exc:  # keep going; the row is marked failed in the DB
            print(f"case {case_id}: FAILED {type(exc).__name__}: {exc}")
            continue
        print(f"case {case_id}: {len(result.records)} knowledge items (evidence {result.evidence_id})")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="question for the knowledge agent")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--limit", type=int, default=None, help="ingest only the first N rows")
    parser.add_argument("--skip-ingest", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.WARNING)

    store = KnowledgeStore.from_env()
    try:
        await store.init_schema()
        if not args.skip_ingest:
            await ingest(store, args.csv, args.limit)
        if args.question:
            answer = await KnowledgeAgentService.from_env(store).ask(args.question)
            print("\n" + answer.answer + "\n")
            for citation in answer.citations:
                print(
                    f"[K:{citation.knowledge_id}] ({citation.type}) {citation.subject}: "
                    f"{citation.claim} — evidence {citation.evidence_id}"
                )
    finally:
        await store.aclose()


if __name__ == "__main__":
    asyncio.run(main())
