"""Steps the full-split runners (train.py, holdout.py) share.

A full-split run extracts its split's conversations under a new run ID next
to the records of the runs it reuses and the already indexed manuals; both
must stay unchanged, and consolidation may only change the new run's
records. Retrieval searches the union, as the runtime would.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import text

from backend.knowledge_bases.base import PROJECT_ROOT
from backend.knowledge_bases.industrial_qa import SPEC
from backend.knowledge_bases.industrial_qa.benchmark.adapter import CompleteAnswer, ScopedRetrieval, benchmark_scope
from backend.knowledge_bases.industrial_qa.benchmark.data import corpus_chunks, save
from backend.knowledge_bases.industrial_qa.benchmark.evaluation import evaluate
from backend.knowledge_bases.industrial_qa.benchmark.ingest import ingest
from backend.knowledge_bases.industrial_qa.benchmark.manifest import (
    EXTRACTION_SOURCES, PIPELINE_SOURCES, RUNNER_SOURCES, file_hashes, models, source_paths, split_inputs,
)
from backend.knowledge_bases.industrial_qa.benchmark.predictions import answer
from backend.knowledge_bases.industrial_qa.benchmark.runs import require_official_database, run_filter, unprocessed
from backend.knowledge_bases.industrial_qa.config import add_runner_arguments, runner_config
from backend.src.storage import KnowledgeStore


COMMANDS = ("select", "ingest", "answer", "evaluate", "all")
# Fields of a reused record that must not change while it is reused.
RECORD_FIELDS = ("subject", "claim", "context", "type", "evidence_id")
_FINGERPRINT = ("SELECT count(*) AS rows, md5(string_agg(md5(row_to_json(t)::text), '' ORDER BY id)) "
                "AS fingerprint FROM ")


def sources(entry_point):
    """Code and prompts a full-split run records and snapshots."""
    return source_paths(entry_point, "benchmark/runner.py", *RUNNER_SOURCES, *EXTRACTION_SOURCES, *PIPELINE_SOURCES)


def write_question_ids(args, run):
    args.question_ids = args.run_dir / "question_ids.txt"
    args.question_ids.write_text("\n".join(run["question_ids"]) + "\n")


def manifest(args, run, files, **details):
    """The settings, inputs and sources of a full-split run; `details` name its reuse."""
    return {
        "policy": args.config.name, "database": SPEC.database, **details,
        "run_id": run["run_id"], "split": run["split"],
        "question_ids": run["question_ids"], "conversation_ids": run["conversation_ids"],
        "reused_run_ids": run["reused_run_ids"], "manual_index_rebuilt": False,
        "retrieval_scope": benchmark_scope(run), "consolidation_scope": run_filter(run),
        "k": args.k, "per_conversation": args.per_conversation,
        "context_default_characters": args.config.retrieval.context_characters, "config": args.config.provenance(),
        "concurrency": args.concurrency, "batch_size": args.batch_size, "verify": args.verify,
        "models": models(),
        "input_sha256": file_hashes(split_inputs(run["split"])),
        "source_sha256": file_hashes(files),
    }


async def inventory(store, run_ids):
    """Fingerprints of the shared manuals and the reused records."""
    result = {}
    async with store.engine.connect() as connection:
        for name, query, params in (
            ("manual_chunks", _FINGERPRINT + "chunks_full t", {}),
            ("reused_records", _FINGERPRINT + "knowledge_items t WHERE context->>'benchmark_run_id' = ANY(:runs)",
             {"runs": run_ids}),
        ):
            result[name] = dict((await connection.execute(text(query), params)).mappings().one())
    return result


async def check_reused_records(store, expected):
    """`expected` maps record IDs to their run.json rows."""
    rows = await store.get_knowledge([UUID(key) for key in expected])
    if {str(row.id) for row in rows} != set(expected):
        raise ValueError("Reused records are missing in the database; restore their run's snapshot first")
    for row in rows:
        actual = row.model_dump(mode="json")
        if any(actual[key] != expected[str(row.id)][key] for key in RECORD_FIELDS):
            raise ValueError("Reused record content changed")


async def execute(args, run, reused_records, *, budgets=None):
    """Ingest the missing conversations, answer and evaluate, keeping reused data unchanged.

    `budgets` maps a question to the context characters it may use.
    """
    if args.command == "select":
        return 0
    store = await KnowledgeStore.for_kb(require_official_database(SPEC), init_schema=False)
    try:
        await check_reused_records(store, reused_records)
        before = await inventory(store, run["reused_run_ids"])
    finally:
        await store.aclose()
    if before["manual_chunks"]["rows"] != len(corpus_chunks()):
        raise ValueError("The existing manual index is incomplete; refusing to rebuild implicitly")
    path = args.run_dir / "inventory_before.json"
    if path.exists() and json.loads(path.read_text()) != before:
        raise ValueError("Manuals or reused records changed")
    save(path, before)
    if args.command in ("ingest", "all"):
        if code := await ingest(run, args.run_dir, args.config, init_schema=False, verify=args.verify,
                                concurrency=args.concurrency, consolidate=args.config.ingestion.consolidate,
                                batch_size=args.batch_size):
            return code
    if args.command in ("answer", "evaluate", "all") and unprocessed(run):
        raise ValueError(f"All {run['split']} conversations must be ingested before evaluation")
    if args.command in ("answer", "all"):
        if code := await answer(args, run, retrieve=ScopedRetrieval(budgets, scope=benchmark_scope(run),
                                                                    config=args.config),
                                generate=CompleteAnswer(args.config), init_schema=False, checkpoint_every=4):
            return code
    if args.command in ("evaluate", "all"):
        if code := evaluate(args, run):
            return code
    store = await KnowledgeStore.for_kb(require_official_database(SPEC), init_schema=False)
    try:
        after = await inventory(store, run["reused_run_ids"])
    finally:
        await store.aclose()
    if after != before:
        raise ValueError(f"Manual chunks or reused records changed during the {run['split']} run")
    save(args.run_dir / "inventory_after.json", {"inventory": after, "unchanged": True})
    return 0


def build_parser(description, reuse_flag, reuse_help):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(reuse_flag, type=Path, required=True, help=reuse_help)
    add_runner_arguments(parser)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--verify", choices=("quotes", "llm"), help="override [ingestion] verify")
    parser.add_argument("--skip-llm", action="store_true")
    return parser


def main(parser, run_async):
    """Parse the arguments, load the config and .env, and run `run_async(args)`."""
    args = parser.parse_args()
    try:
        args.config = runner_config(args)
    except ValueError as exc:
        parser.error(str(exc))
    if min(args.concurrency, args.batch_size) < 1:
        parser.error("Invalid batching options")
    load_dotenv(PROJECT_ROOT / ".env")
    return asyncio.run(run_async(args))
