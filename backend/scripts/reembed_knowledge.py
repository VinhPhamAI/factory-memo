"""Recompute every knowledge embedding with the configured embedding model.

Run after changing EMBEDDING_MODEL / EMBEDDING_DIM. Records keep their ids and
content; only the embedding column is rewritten (and resized if needed).

Example:
    uv run --env-file .env python -m backend.scripts.reembed_knowledge
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from backend.src.storage import KnowledgeStore, StorageError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=__doc__.splitlines()[0])


async def run() -> int:
    store = KnowledgeStore.from_env()
    try:
        count = await store.reembed_knowledge()
        # Recreates indexes (HNSW when the new dimension allows it) and
        # verifies the column now matches EMBEDDING_DIM.
        await store.init_schema()
    finally:
        await store.aclose()
    print(f"Re-embedded {count} knowledge items")
    return 0


def main() -> int:
    build_parser().parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run())
    except (OSError, ValueError, StorageError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
