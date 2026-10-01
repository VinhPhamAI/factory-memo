"""Manage and query knowledge bases, each stored in its own database.

Examples:
    uv run python -m backend.scripts.kb list
    uv run python -m backend.scripts.kb ingest maintenance --limit 5
    uv run python -m backend.scripts.kb ingest maintenance --batch-size 8
    uv run python -m backend.scripts.kb ingest enterprise_rag --limit 20 --batch-size 8
    uv run python -m backend.scripts.kb ingest maintenance --file transcript.txt
    uv run python -m backend.scripts.kb query maintenance "How do I fix the unstable sensor on M01?"
    uv run python -m backend.scripts.kb reembed maintenance
    uv run python -m backend.scripts.kb reset maintenance --yes
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

from backend.knowledge_bases import KNOWLEDGE_BASES, KnowledgeBaseSpec, get_kb
from backend.knowledge_bases.base import PROJECT_ROOT
from backend.src.agents.agent import KnowledgeAgentError, KnowledgeAgentService
from backend.src.services.knowledge_extraction import KnowledgeExtractionService
from backend.src.services.knowledge_ingestion import (
    IngestionInput,
    IngestionResult,
    KnowledgeIngestionService,
)
from backend.src.storage import KnowledgeStore, StorageError
from backend.src.storage.databases import (
    DatabaseAdminError,
    database_exists,
    drop_database,
    server_url_from_env,
)


DEFAULT_TRANSCRIPT_COLUMN = "raw_conversation"
DEFAULT_STATED_BY_COLUMN = "stated_by"
# Headroom for reasoning models, whose thinking tokens count against max_tokens.
EXTRACTION_MAX_TOKENS = 8192
TEXT_SUFFIXES = {".txt", ".md"}


@dataclass(frozen=True)
class TranscriptInput:
    transcript: str
    stated_by: str | None
    metadata: dict[str, str]
    label: str


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


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="List registered knowledge bases")

    ingest = commands.add_parser("ingest", help="Extract and store knowledge from transcripts")
    ingest.add_argument("kb")
    ingest.add_argument(
        "--file",
        type=Path,
        help=".txt, .md, or .csv input (default: the knowledge base's dataset)",
    )
    ingest.add_argument(
        "--transcript-column",
        help="CSV transcript column (default: from the knowledge base's dataset)",
    )
    ingest.add_argument(
        "--stated-by-column",
        help="CSV speaker column when present (default: from the knowledge base's dataset)",
    )
    ingest.add_argument(
        "--stated-by",
        help="Speaker applied to every input; overrides the CSV speaker column",
    )
    ingest.add_argument("--limit", type=_positive_int, help="Process at most this many transcripts")
    ingest.add_argument(
        "--batch-size",
        type=_positive_int,
        default=1,
        help="Transcripts per batch: extracted concurrently, embedded in one call "
        "(default: 1, one at a time)",
    )
    ingest.add_argument(
        "--concurrency",
        type=_positive_int,
        help="Maximum concurrent LLM extraction calls (default: --batch-size)",
    )
    ingest.add_argument(
        "--no-resume",
        action="store_true",
        help="Ingest rows whose dataset id is already processed again "
        "(by default they are skipped, so an interrupted run can continue)",
    )
    ingest.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the batch with the first failed transcript instead of continuing",
    )

    query = commands.add_parser("query", help="Ask the knowledge agent one question")
    query.add_argument("kb")
    query.add_argument("question")

    reembed = commands.add_parser(
        "reembed", help="Recompute embeddings after changing EMBEDDING_MODEL / EMBEDDING_DIM"
    )
    reembed.add_argument("kb")

    reset = commands.add_parser("reset", help="Drop the knowledge base's database")
    reset.add_argument("kb")
    reset.add_argument("--yes", action="store_true", help="Confirm deleting all its data")
    return parser


async def list_kbs() -> int:
    try:
        server_url = server_url_from_env()
    except ValueError:
        server_url = None
    for spec in KNOWLEDGE_BASES.values():
        state = "unknown"
        if server_url:
            try:
                state = "exists" if await database_exists(server_url, spec.database) else "not created"
            except DatabaseAdminError:
                state = "server unavailable"
        print(f"{spec.name}\tdatabase={spec.database} ({state})\t{spec.description.strip()}")
    return 0


async def ingest(spec: KnowledgeBaseSpec, args: argparse.Namespace) -> int:
    dataset = spec.dataset
    path = args.file or (dataset.path if dataset else None)
    if path is None:
        raise ValueError(f"Knowledge base {spec.name!r} has no default dataset; pass --file")
    inputs = read_transcripts(
        path,
        transcript_column=args.transcript_column
        or (dataset.transcript_column if dataset else DEFAULT_TRANSCRIPT_COLUMN),
        stated_by_column=args.stated_by_column
        or (dataset.stated_by_column if dataset else DEFAULT_STATED_BY_COLUMN),
        stated_by=args.stated_by,
    )
    # Read (and validate) the input before creating the database.
    items = list(inputs)
    if not items:
        raise ValueError(f"Input file contains no transcripts: {path}")
    id_column = dataset.id_column if dataset else None
    context_columns = dataset.context_columns if dataset else ()

    extractor = KnowledgeExtractionService.from_env(
        spec.extract_prompt, max_tokens=EXTRACTION_MAX_TOKENS
    )
    store = await KnowledgeStore.for_kb(spec)
    concurrency = args.concurrency or args.batch_size
    succeeded = failed = knowledge_items = 0
    started = time.perf_counter()
    try:
        if id_column and not args.no_resume:
            done = await store.processed_metadata_values(id_column)
            remaining = [item for item in items if item.metadata.get(id_column) not in done]
            if skipped := len(items) - len(remaining):
                print(f"[{spec.name}] Skipping {skipped} already processed rows ({id_column})")
            items = remaining
        if args.limit:
            items = items[: args.limit]
        ingestion = KnowledgeIngestionService(
            extractor, store,
            extraction_metadata={"version": spec.extraction_version, **extractor.provenance},
        )
        for start in range(0, len(items), args.batch_size):
            batch = items[start : start + args.batch_size]
            results = await ingestion.ingest_batch(
                [
                    IngestionInput(
                        item.transcript,
                        stated_by=item.stated_by,
                        metadata={**item.metadata, "kb": spec.name},
                        context={
                            column: item.metadata[column]
                            for column in context_columns
                            if column in item.metadata
                        },
                    )
                    for item in batch
                ],
                concurrency=concurrency,
            )
            # Failed transcripts are marked failed in the DB; the rest are kept.
            for item, result in zip(batch, results, strict=True):
                if isinstance(result, IngestionResult):
                    succeeded += 1
                    knowledge_items += len(result.records)
                    print(
                        f"{item.label}: {len(result.records)} knowledge items "
                        f"(evidence {result.evidence_id})"
                    )
                else:
                    failed += 1
                    print(
                        f"{item.label}: FAILED {type(result).__name__}: {result}",
                        file=sys.stderr,
                    )
            if failed and args.fail_fast:
                break
    finally:
        await store.aclose()

    elapsed = time.perf_counter() - started
    print(
        f"[{spec.name}] Ingestion complete: {succeeded} succeeded, {failed} failed, "
        f"{knowledge_items} knowledge items in {elapsed:.1f}s "
        f"(batch size {args.batch_size}, concurrency {concurrency})"
    )
    return 1 if failed else 0


async def query(spec: KnowledgeBaseSpec, question: str) -> int:
    store = await KnowledgeStore.for_kb(spec)
    try:
        answer = await KnowledgeAgentService.from_kb(spec, store).ask(question)
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


async def reembed(spec: KnowledgeBaseSpec) -> int:
    # init_schema would reject a column whose size no longer matches EMBEDDING_DIM.
    store = await KnowledgeStore.for_kb(spec, init_schema=False)
    try:
        count = await store.reembed_knowledge()
        # Recreates indexes (HNSW when the new dimension allows it) and
        # verifies the column now matches EMBEDDING_DIM.
        await store.init_schema()
    finally:
        await store.aclose()
    print(f"[{spec.name}] Re-embedded {count} knowledge items")
    return 0


async def reset(spec: KnowledgeBaseSpec, *, confirmed: bool) -> int:
    if not confirmed:
        print(
            f"This drops database {spec.database} and all of its knowledge; "
            "re-run with --yes to confirm.",
            file=sys.stderr,
        )
        return 2
    dropped = await drop_database(server_url_from_env(), spec.database)
    print(f"[{spec.name}] {'Dropped' if dropped else 'No'} database {spec.database}")
    return 0


async def run(args: argparse.Namespace) -> int:
    if args.command == "list":
        return await list_kbs()
    spec = get_kb(args.kb)
    if args.command == "ingest":
        return await ingest(spec, args)
    if args.command == "query":
        return await query(spec, args.question)
    if args.command == "reembed":
        return await reembed(spec)
    return await reset(spec, confirmed=args.yes)


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args))
    except (
        OSError,
        ValueError,
        StorageError,
        DatabaseAdminError,
        KnowledgeAgentError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
