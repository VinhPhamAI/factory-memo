"""Ingest transcripts from a text or CSV file into the knowledge base.

Examples:
    uv run --env-file .env python -m backend.scripts.ingest_knowledge transcript.txt
    uv run --env-file .env python -m backend.scripts.ingest_knowledge conversations.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

from backend.src.services.knowledge_extraction import KnowledgeExtractionService
from backend.src.services.knowledge_ingestion import KnowledgeIngestionService
from backend.src.storage import KnowledgeStore, StorageError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSCRIPT_COLUMN = "raw_conversation"
DEFAULT_STATED_BY_COLUMN = "stated_by"
EXTRACTION_MAX_TOKENS = 8192
TEXT_SUFFIXES = {".txt", ".md"}


@dataclass(frozen=True)
class TranscriptInput:
    transcript: str
    stated_by: str | None
    metadata: dict[str, str]
    label: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path, help=".txt, .md, or .csv input file")
    parser.add_argument(
        "--transcript-column",
        default=DEFAULT_TRANSCRIPT_COLUMN,
        help=f"CSV transcript column (default: {DEFAULT_TRANSCRIPT_COLUMN})",
    )
    parser.add_argument(
        "--stated-by-column",
        default=DEFAULT_STATED_BY_COLUMN,
        help=f"CSV speaker column when present (default: {DEFAULT_STATED_BY_COLUMN})",
    )
    parser.add_argument(
        "--stated-by",
        help="Speaker applied to every input; overrides the CSV speaker column",
    )
    parser.add_argument("--limit", type=int, help="Process at most this many transcripts")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed transcript instead of continuing",
    )
    return parser


def read_transcripts(
    path: Path,
    *,
    transcript_column: str = DEFAULT_TRANSCRIPT_COLUMN,
    stated_by_column: str = DEFAULT_STATED_BY_COLUMN,
    stated_by: str | None = None,
) -> Iterator[TranscriptInput]:
    """Yield normalized ingestion inputs without touching external services."""

    if not path.is_file():
        raise ValueError(f"Input file does not exist: {path}")

    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        transcript = path.read_text(encoding="utf-8-sig").strip()
        if not transcript:
            raise ValueError(f"Input file is empty: {path}")
        yield TranscriptInput(
            transcript=transcript,
            stated_by=stated_by,
            metadata={"source_file": path.name},
            label=path.name,
        )
        return

    if suffix != ".csv":
        raise ValueError("Unsupported input format; expected .txt, .md, or .csv")

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if transcript_column not in (reader.fieldnames or []):
            raise ValueError(
                f"CSV must contain transcript column {transcript_column!r}"
            )
        for row_number, row in enumerate(reader, start=2):
            transcript = (row.get(transcript_column) or "").strip()
            if not transcript:
                raise ValueError(f"CSV row {row_number} has an empty transcript")
            row_stated_by = stated_by
            if row_stated_by is None:
                row_stated_by = (row.get(stated_by_column) or "").strip() or None
            metadata = {
                key: value
                for key, raw_value in row.items()
                if key not in {transcript_column, stated_by_column}
                and raw_value is not None
                and (value := raw_value.strip())
            }
            metadata.update({"source_file": path.name, "source_row": str(row_number)})
            yield TranscriptInput(
                transcript=transcript,
                stated_by=row_stated_by,
                metadata=metadata,
                label=f"{path.name}:{row_number}",
            )


async def run(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be a positive integer")

    input_stream = read_transcripts(
        args.file,
        transcript_column=args.transcript_column,
        stated_by_column=args.stated_by_column,
        stated_by=args.stated_by,
    )
    limited_inputs = islice(input_stream, args.limit) if args.limit else input_stream
    first_input = next(limited_inputs, None)
    if first_input is None:
        raise ValueError(f"Input file contains no transcripts: {args.file}")
    inputs = chain((first_input,), limited_inputs)

    extractor = KnowledgeExtractionService.from_env(max_tokens=EXTRACTION_MAX_TOKENS)
    store = KnowledgeStore.from_env()
    succeeded = 0
    failed = 0
    try:
        await store.init_schema()
        ingestion = KnowledgeIngestionService(extractor, store)
        for item in inputs:
            try:
                result = await ingestion.ingest(
                    item.transcript,
                    stated_by=item.stated_by,
                    metadata=item.metadata,
                )
            except Exception as exc:
                failed += 1
                print(
                    f"{item.label}: FAILED {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                if args.fail_fast:
                    break
                continue
            succeeded += 1
            print(
                f"{item.label}: {len(result.records)} knowledge items "
                f"(evidence {result.evidence_id})"
            )
    finally:
        await store.aclose()

    print(f"Ingestion complete: {succeeded} succeeded, {failed} failed")
    return 1 if failed else 0


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args))
    except (OSError, ValueError, StorageError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
