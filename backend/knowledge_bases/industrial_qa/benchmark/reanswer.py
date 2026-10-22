"""Re-answer a completed run from its frozen sources with the current answer writer.

Retrieval and reranking are not repeated: each question gets exactly the
sources the --from-run-dir answers were written from, re-read from the same
database by record/chunk ID and rendered again. A writer change is then
measured without reranker variance. Extraction is unchanged, so only answers
are judged (no fact judge).

python -m backend.knowledge_bases.industrial_qa.benchmark.reanswer --from-run-dir OLD --run-dir NEW
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
from uuid import UUID

from dotenv import load_dotenv

from backend.knowledge_bases.base import PROJECT_ROOT
from backend.knowledge_bases.industrial_qa import SPEC
from backend.knowledge_bases.industrial_qa.benchmark.adapter import CompleteAnswer
from backend.knowledge_bases.industrial_qa.benchmark.evaluation import evaluate
from backend.knowledge_bases.industrial_qa.benchmark.manifest import (
    PIPELINE_SOURCES, RUNNER_SOURCES, file_hashes, source_paths, write_manifest,
)
from backend.knowledge_bases.industrial_qa.benchmark.predictions import answer
from backend.knowledge_bases.industrial_qa.retrieval import Source


class FrozenRetrieval:
    """The previous run's selected sources, in the order the writer saw them."""

    def __init__(self, answers):
        self.answers = {answer["question"]: answer for answer in answers}

    async def __call__(self, question, store, documents, run, args, by_evidence, conversations, chunks):
        previous = self.answers[question]
        labels = previous["sources"]
        records = {str(r.id): r for r in await store.get_knowledge(
            [UUID(s["knowledge_id"]) for label, s in labels.items() if label.startswith("K")])}
        expected = {s["knowledge_id"] for label, s in labels.items() if label.startswith("K")}
        if expected - records.keys():
            raise ValueError("Frozen records have been overwritten in kb_industrial_qa; restore the source snapshot first")
        sources = {}
        for label, details in labels.items():
            if label.startswith("K"):
                source = Source(label, record=records[details["knowledge_id"]])
            else:
                passages = await documents.get_document(details["chunk_id"])
                if len(passages) != 1:
                    raise ValueError(f"Manual document {details['chunk_id']} is not one chunk")
                source = Source(label, passage=passages[0])
            sources[label] = {**details, "text": source.text}
        size = sum(len(s["text"]) for s in sources.values())
        if size != previous["context_characters"]:
            raise ValueError(f"Rebuilt sources differ from the previous run: {size} != "
                             f"{previous['context_characters']} characters for {previous['question_id']}")
        return sources, previous["retrieved_uncited"]


def prepare(args):
    source = args.from_run_dir
    manifest = json.loads((source / "manifest.json").read_text())
    database = SPEC.database
    args.run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("run.json", "question_ids.txt"):
        if not (args.run_dir / name).exists():
            shutil.copyfile(source / name, args.run_dir / name)
    files = source_paths("benchmark/reanswer.py", *RUNNER_SOURCES, *PIPELINE_SOURCES)
    record = {
        "policy": "frozen sources + current answer writer", "from_run_dir": str(source.resolve()),
        "database": database, "source_database": manifest.get("database"), "run_id": manifest["run_id"],
        "models": {key: os.getenv(key) for key in ("OPENROUTER_MODEL", "ANSWER_MODEL", "ANSWER_THINKING",
                                                   "EXTRACTION_MODEL", "MODELARK_MODELNAME")},
        "source_sha256": file_hashes(files),
    }
    write_manifest(args.run_dir, record, changed="Writer or source run changed; use a new --run-dir")
    return json.loads((args.run_dir / "run.json").read_text())


async def execute(args):
    run = prepare(args)
    answers = json.loads((args.from_run_dir / "answers.json").read_text())
    if code := await answer(args, run, retrieve=FrozenRetrieval(answers), generate=CompleteAnswer(),
                                  init_schema=False, checkpoint_every=4):
        return code
    return evaluate(args, run, facts=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-run-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--skip-llm", action="store_true", help="score retrieval and MC only")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    load_dotenv(PROJECT_ROOT / ".env")
    return asyncio.run(execute(args))


if __name__ == "__main__":
    raise SystemExit(main())
