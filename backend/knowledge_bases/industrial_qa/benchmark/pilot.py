"""Run a small sample of the benchmark/ training questions end to end.

    uv run python -m backend.knowledge_bases.industrial_qa.benchmark.pilot all --run-dir data/industrial_qa_runs/train-pilot10

Steps (each resumes from <run-dir>/run.json; `all` runs them in order):

- select: pick --sample questions of the train split (of one --source only, if
  given), one per conversation, spread over the knowledge_source x difficulty
  cells; or the questions listed in --question-ids, to rerun an earlier sample.
- ingest: index every manual chunk as a document (embeddings only), and
  extract knowledge from the sampled questions' conversations (plus
  --distractors other conversations of the split): Case Cards and lessons,
  normalized to the equipment catalog (catalog.py) and the failure-mode
  ontology, verified against the transcript and
  consolidated with the records of the same run only (--no-consolidate skips
  it).
- renormalize: rescope the stored records of --from-run-dir with the current
  equipment catalog and ontology, without extracting again, and copy its
  sample into a new --run-dir; then run answer and evaluate there to measure
  the catalog alone. The records are updated in place (they belong to the run
  id), so the old run keeps its result files but not its database state.
- answer: answer each question with the official retrieval and answer
  pipeline (adapter.py; its knowledge_source is not public), and
  write predictions.jsonl for benchmark/evaluate.py. The evidence ranking puts the sources the answer cites
  first, in citation order, then the others; predictions_uncited.jsonl keeps
  the ranking before that. Questions answered by an earlier attempt are not
  asked again.
- evaluate: score the sample with benchmark/evaluate.py (retrieval and MC;
  answers and extracted facts too when a judge is configured: MODELARK_*,
  else the EXTRACTION_* / OPENROUTER_* model) into results.json, plus the gold
  evidence among the turns of the top k records, the gold expert facts stated
  at a turn some extracted record points to, and ingestion quality
  (verification, scope, catalog changes, case/lesson overlap, consolidation
  errors).

The system under test reads only benchmark/public/; gold files are used to
choose the sample and to score it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.knowledge_bases.base import PROJECT_ROOT
from backend.knowledge_bases.industrial_qa.benchmark.data import gold_questions, save
from backend.knowledge_bases.industrial_qa.benchmark.evaluation import evaluate
from backend.knowledge_bases.industrial_qa.benchmark.ingest import ingest
from backend.knowledge_bases.industrial_qa.benchmark.predictions import answer
from backend.knowledge_bases.industrial_qa.benchmark.renormalize import renormalize, start_renormalized_run
from backend.knowledge_bases.industrial_qa.config import config_from
from backend.src.services.knowledge_verification import VerifyMode


SPLITS = ("train",)


def select_questions(questions: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """n questions from distinct conversations, round-robin over the cells.

    Cells are knowledge_source x difficulty; a seeded shuffle within each cell
    makes the sample reproducible.
    """

    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for question in sorted(questions, key=lambda q: q["question_id"]):
        cells[(question["knowledge_source"], question["difficulty"])].append(question)
    rng = random.Random(seed)
    for members in cells.values():
        rng.shuffle(members)
    picked: list[dict[str, Any]] = []
    conversations: set[str] = set()
    while len(picked) < n:
        added = False
        for cell in sorted(cells):
            while cells[cell] and cells[cell][0]["conversation_id"] in conversations:
                cells[cell].pop(0)
            if cells[cell] and len(picked) < n:
                question = cells[cell].pop(0)
                picked.append(question)
                conversations.add(question["conversation_id"])
                added = True
        if not added:
            break
    return picked


def select(args: argparse.Namespace) -> dict[str, Any]:
    path = args.run_dir / "run.json"
    if path.exists():
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("split") != "train":
            raise SystemExit("Pilot runs require the train split; use a new --run-dir for this pilot")
        train = gold_questions("train")
        train_ids = {q["question_id"] for q in train}
        train_convs = {q["conversation_id"] for q in train}
        if (set(run["question_ids"]) - train_ids
                or set(run["conversation_ids"]) - train_convs):
            raise SystemExit("Pilot checkpoint contains inputs outside the train split; use a new --run-dir")
        print(f"Sample of {run['run_id']}: {len(run['question_ids'])} questions, "
              f"{len(run['conversation_ids'])} conversations (from {path})")
        return run
    if args.split != "train":
        raise SystemExit("Pilot runs require the train split")
    questions = gold_questions("train")
    if args.question_ids:
        by_id = {q["question_id"]: q for q in questions}
        wanted = args.question_ids.read_text(encoding="utf-8").split()
        if unknown := [qid for qid in wanted if qid not in by_id]:
            raise SystemExit(f"Not train questions: {', '.join(unknown)}")
        picked = [by_id[qid] for qid in wanted]
    else:
        picked = select_questions(
            [q for q in questions if args.source in (None, q["knowledge_source"])],
            args.sample, args.seed,
        )
    conversation_ids = [q["conversation_id"] for q in picked]
    others = sorted({q["conversation_id"] for q in questions} - set(conversation_ids))
    distractors = random.Random(args.seed).sample(others, min(args.distractors, len(others)))
    run = {
        "run_id": f"industrial-qa-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "split": args.split,
        "seed": args.seed,
        "source": args.source,
        "question_ids": [q["question_id"] for q in picked],
        "conversation_ids": conversation_ids + distractors,
        "distractor_conversation_ids": distractors,
        "consolidate": not args.no_consolidate,
        "conversations": {},
    }
    save(path, run)
    (args.run_dir / "question_ids.txt").write_text(
        "".join(f"{qid}\n" for qid in run["question_ids"]), encoding="utf-8"
    )
    for question in picked:
        print(f"{question['question_id']} {question['knowledge_source']}/{question['difficulty']} "
              f"{question['conversation_id']}: {question['question']}")
    print(f"Selected {len(picked)} questions, {len(run['conversation_ids'])} conversations "
          f"({len(distractors)} distractors) into {path}")
    return run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", choices=("select", "ingest", "renormalize", "answer", "evaluate", "all")
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--from-run-dir", type=Path,
        help="renormalize: the run whose stored records to rescope; its sample is "
             "copied into a new --run-dir",
    )
    parser.add_argument("--split", choices=SPLITS, default="train")
    parser.add_argument("--sample", type=int, default=10, help="questions to run (default 10)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--question-ids", type=Path,
        help="file of train question ids to run instead of a sampled selection",
    )
    parser.add_argument(
        "--source", choices=("expert_only", "expert_and_docs", "docs_only"),
        help="only questions of this knowledge_source",
    )
    parser.add_argument(
        "--distractors", type=int, default=0,
        help="other conversations of the split to extract, so the sample's "
             "conversations are not the only knowledge searched",
    )
    parser.add_argument("--verify", choices=[m.value for m in VerifyMode], default="quotes")
    parser.add_argument("--config", type=Path, help="industrial QA pipeline settings (default: config.toml)")
    parser.add_argument("--k", type=int, default=8, help="records and chunks searched per question")
    parser.add_argument(
        "--per-conversation", type=int, default=3,
        help="most records of one conversation among the k (0: no cap)",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--skip-llm", action="store_true", help="evaluate without the answer and fact judge"
    )
    parser.add_argument(
        "--no-consolidate", action="store_true",
        help="store each conversation's records as new (new runs only)",
    )
    return parser


async def run_async(args: argparse.Namespace) -> int:
    if args.command == "renormalize":
        start_renormalized_run(args)
    run = select(args)
    if args.command == "select":
        return 0
    if args.command == "renormalize":
        return await renormalize(args, run)
    if args.command in ("ingest", "all"):
        if code := await ingest(run, args.run_dir, config_from(args), index_manuals_first=True,
                                verify=args.verify, concurrency=args.concurrency,
                                consolidate=run.get("consolidate")):
            return code
    if args.command in ("answer", "all"):
        if code := await answer(args, run):
            return code
    if args.command in ("evaluate", "all"):
        return evaluate(args, run)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if (args.sample < 1 or args.k < 1 or args.concurrency < 1 or args.distractors < 0
            or args.per_conversation < 0):
        raise SystemExit("--sample, --k and --concurrency must be positive")
    load_dotenv(PROJECT_ROOT / ".env")
    return asyncio.run(run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
