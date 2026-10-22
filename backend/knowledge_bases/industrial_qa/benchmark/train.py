"""Ingest all train conversations and evaluate the official pipeline on every train question.

All runs write to kb_industrial_qa, replacing unrelated older runs.
Reuse baseline records and already indexed manuals; only missing conversations
are extracted. New records use a separate run ID. Retrieval reads their union
with baseline, while consolidation may only change the new run's records.
Each question keeps the context budget it had in --from-run-dir.

python -m backend.knowledge_bases.industrial_qa.benchmark.train all --run-dir PATH --from-run-dir SOURCE
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from backend.knowledge_bases.base import PROJECT_ROOT
from backend.knowledge_bases.industrial_qa.benchmark import runner
from backend.knowledge_bases.industrial_qa.benchmark.data import gold_questions, public_questions, save
from backend.knowledge_bases.industrial_qa.benchmark.manifest import (
    EXTRACTION_SOURCES, project_path, source_paths, write_manifest,
)
from backend.knowledge_bases.industrial_qa.config import sha


def make_run(questions, baseline, directory):
    question_ids = sorted(q["question_id"] for q in questions)
    conversation_ids = sorted({q["conversation_id"] for q in questions})
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("Train question IDs are duplicated")
    if baseline["split"] != "train" or set(baseline["conversation_ids"]) - set(conversation_ids):
        raise ValueError("Reuse checkpoint contains conversations outside train")
    reused = {cid: value for cid, value in baseline["conversations"].items()
              if value.get("status") == "processed" and cid in conversation_ids}
    # A JSON copy preserves the historical checkpoint unchanged.
    return {
        "run_id": f"industrial-qa-train-full-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{sha(str(directory))[:8]}",
        "created_at": datetime.now(timezone.utc).isoformat(), "split": "train", "seed": 42,
        "source": None, "question_ids": question_ids, "conversation_ids": conversation_ids,
        "distractor_conversation_ids": [], "consolidate": True,
        "reused_run_ids": list(dict.fromkeys([baseline["run_id"], *baseline.get("reused_run_ids", [])])), "reused_conversation_ids": sorted(reused),
        "new_conversation_ids": sorted(set(conversation_ids) - reused.keys()),
        "conversations": json.loads(json.dumps(reused)), "extraction": baseline["extraction"],
    }


def source_hashes(directory, manifest):
    """Repairs may inherit the unchanged extraction contract from a parent run."""
    result, visited = {}, set()
    while True:
        directory = directory.resolve()
        if directory in visited:
            raise ValueError("Source manifest ancestry contains a cycle")
        visited.add(directory)
        for path, digest in manifest.get("source_sha256", {}).items():
            result.setdefault(path, digest)  # the newest declared version wins
        parent = manifest.get("from_run_dir")
        if not parent:
            return result
        parent_directory = Path(parent)
        if not parent_directory.is_absolute():
            parent_directory = directory / parent_directory
        parent_file = parent_directory / "manifest.json"
        if not parent_file.is_file():
            return result
        contents = parent_file.read_text()
        if expected := manifest.get("source_manifest_sha256"):
            if sha(contents) != expected:
                raise ValueError("Source parent manifest changed since this snapshot")
        directory, manifest = parent_directory, json.loads(contents)


def prepare(args):
    baseline = json.loads((args.from_run_dir / "run.json").read_text())
    previous = json.loads((args.from_run_dir / "manifest.json").read_text())
    questions = gold_questions("train")
    public = public_questions("train")
    if {q["question_id"] for q in questions} != set(public):
        raise ValueError("Public train questions do not match the train split")
    test_convs = {q["conversation_id"] for q in gold_questions("test")}
    if {q["conversation_id"] for q in questions} & test_convs:
        raise ValueError("Train and test conversations overlap")
    for path, expected in previous["input_sha256"].items():
        if path.startswith("benchmark/public/") and sha((PROJECT_ROOT / path).read_text()) != expected:
            raise ValueError(f"Reuse inputs changed: {path}")
    hashes = source_hashes(args.from_run_dir, previous)
    for path in source_paths(*EXTRACTION_SOURCES):
        expected = hashes.get(project_path(path))
        if expected is None:
            raise ValueError(f"Source snapshot has no extraction contract hash: {path.name}")
        if sha(path.read_text()) != expected:
            raise ValueError(f"Reuse extraction contract changed: {path.name}")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    path = args.run_dir / "run.json"
    run = json.loads(path.read_text()) if path.exists() else make_run(questions, baseline, args.run_dir)
    if (run["question_ids"] != sorted(public)
            or run["conversation_ids"] != sorted({q["conversation_id"] for q in questions})
            or run["reused_run_ids"] != list(dict.fromkeys([baseline["run_id"], *baseline.get("reused_run_ids", [])]))):
        raise ValueError("Checkpoint does not cover the complete train split")
    save(path, run)
    runner.write_question_ids(args, run)
    files = runner.sources("benchmark/train.py")
    # Artifacts describe the source dataset, never a target database.
    write_manifest(args.run_dir, runner.manifest(
        args, run, files,
        source_database=previous.get("database"), corpus_policy="replace previous runs in the official database",
        from_run_dir=str(args.from_run_dir.resolve()),
        reused_conversations=len(run["reused_conversation_ids"]), new_conversations=len(run["new_conversation_ids"]),
    ), sources=files, config_path=args.config.path,
        changed="Full-train configuration changed; preserve this run and use a new run directory")
    print(f"Full train: {len(run['question_ids'])} questions, {len(run['conversation_ids'])} conversations; "
          f"reuse {len(run['reused_conversation_ids'])}, new {len(run['new_conversation_ids'])}")
    return run


async def execute(args):
    run = prepare(args)
    reused = {row["id"]: row for cid in run["reused_conversation_ids"]
              for row in run["conversations"][cid]["records"]}
    budgets = None
    if args.command in ("answer", "all"):
        old = json.loads((args.from_run_dir / "answers.json").read_text())
        budgets = {answer["question"]: answer["context_characters"] for answer in old}
    return await runner.execute(args, run, reused, budgets=budgets)


def main():
    parser = runner.build_parser(__doc__, "--from-run-dir", "train run whose extractions are reused")
    return runner.main(parser, execute)


if __name__ == "__main__":
    raise SystemExit(main())
