"""Ask one question against the existing knowledge base.

Example:
    uv run --env-file .env python -m backend.scripts.query_knowledge \
        "How do I fix the unstable sensor on M01?"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from backend.src.agents.agent import KnowledgeAgentError, KnowledgeAgentService
from backend.src.storage import KnowledgeStore, StorageError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", help="Question to answer from stored knowledge")
    return parser


async def run(query: str) -> int:
    store = KnowledgeStore.from_env()
    try:
        answer = await KnowledgeAgentService.from_env(store).ask(query)
    finally:
        await store.aclose()

    print(answer.answer)
    if answer.citations:
        print("\nSources:")
        for citation in answer.citations:
            print(
                f"- [K:{citation.knowledge_id}] ({citation.type}) "
                f"{citation.subject}: {citation.claim} "
                f"— evidence {citation.evidence_id}"
            )
    return 0


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args.query))
    except (OSError, ValueError, KnowledgeAgentError, StorageError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
