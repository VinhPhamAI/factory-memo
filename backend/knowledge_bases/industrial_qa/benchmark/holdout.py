"""Ingest all test conversations and evaluate the official pipeline on every test question.

The held-out check of a frozen solution. Test conversations are extracted
under a new run ID next to the train corpus of --reuse-run-dir, and retrieval
searches their union with the shared manuals, as the runtime would. Train
records and manuals must stay unchanged; consolidation may only change the
test run's records. Each step resumes from run.json.

python -m backend.knowledge_bases.industrial_qa.benchmark.holdout all --run-dir PATH --reuse-run-dir TRAIN
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

from backend.knowledge_bases.industrial_qa.benchmark import runner
from backend.knowledge_bases.industrial_qa.benchmark.data import gold_questions, public_questions, save
from backend.knowledge_bases.industrial_qa.benchmark.manifest import write_manifest


SPLIT = "test"


def make_run(questions, reused):
    return {
        "run_id": f"industrial-qa-test-full-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        "created_at": datetime.now(timezone.utc).isoformat(), "split": SPLIT, "seed": None, "source": None,
        "question_ids": sorted(q["question_id"] for q in questions),
        "conversation_ids": sorted({q["conversation_id"] for q in questions}),
        "distractor_conversation_ids": [], "consolidate": True, "reused_run_ids": reused, "conversations": {},
    }


def prepare(args):
    questions = gold_questions(SPLIT)
    public = public_questions(SPLIT)
    if {q["question_id"] for q in questions} != set(public) or len(public) != len(questions):
        raise ValueError("Public test questions do not match the test split")
    if {q["conversation_id"] for q in questions} & {q["conversation_id"] for q in gold_questions("train")}:
        raise ValueError("Train and test conversations overlap")
    train = json.loads((args.reuse_run_dir / "run.json").read_text())
    if train["split"] != "train":
        raise ValueError("--reuse-run-dir must be a train run")
    reused = list(dict.fromkeys([train["run_id"], *train.get("reused_run_ids", [])]))
    args.run_dir.mkdir(parents=True, exist_ok=True)
    path = args.run_dir / "run.json"
    run = json.loads(path.read_text()) if path.exists() else make_run(questions, reused)
    if run["split"] != SPLIT or run["question_ids"] != sorted(public) or run["reused_run_ids"] != reused:
        raise ValueError("Checkpoint does not cover the complete test split")
    save(path, run)
    runner.write_question_ids(args, run)
    files = runner.sources("benchmark/holdout.py")
    write_manifest(args.run_dir, runner.manifest(
        args, run, files,
        corpus_policy="test run next to the reused train corpus; other runs are replaced",
        reuse_run_dir=str(args.reuse_run_dir.resolve()),
    ), sources=files, config_path=args.config.path,
        changed="Test configuration changed; preserve this run and use a new run directory")
    done = sum(c.get("status") == "processed" for c in run["conversations"].values())
    print(f"Test: {len(run['question_ids'])} questions, {len(run['conversation_ids'])} conversations; {done} extracted")
    return run, train


async def execute(args):
    run, train = prepare(args)
    reused = {row["id"]: row for conversation in train["conversations"].values()
              if conversation.get("status") == "processed" for row in conversation["records"]}
    return await runner.execute(args, run, reused)


def main():
    parser = runner.build_parser(__doc__, "--reuse-run-dir", "train run whose corpus stays searchable")
    return runner.main(parser, execute)


if __name__ == "__main__":
    raise SystemExit(main())
