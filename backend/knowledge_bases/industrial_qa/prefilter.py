"""Train-only experiment: a local cross-encoder before the H1 LLM reranker.

`pool` makes no LLM calls: it rebuilds each train question's H1 candidates
from the existing train records and manuals, then measures how much gold
evidence survives keeping the top N per source by hybrid order or by each
cross-encoder. `answer` runs the full H1+H3 benchmark path on the train split
with or without the prefilter or the `--rerank-mode hybrid` cut, then judges
answers. `compare` tabulates runs.
Test questions and labels are never read.

python -m backend.knowledge_bases.industrial_qa.prefilter pool --run-dir OUT --from-run-dir TRAIN --model M
python -m backend.knowledge_bases.industrial_qa.prefilter answer --run-dir OUT --from-run-dir TRAIN [--model M | --rerank-mode hybrid]
python -m backend.knowledge_bases.industrial_qa.prefilter compare RUN_DIR...
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import json
import os
from pathlib import Path
import shutil
import statistics
from time import monotonic

from dotenv import load_dotenv

from backend.knowledge_bases.industrial_qa import SPEC, pilot
from backend.knowledge_bases.industrial_qa.benchmark_adapter import CompleteAnswer, ScopedRetrieval, benchmark_scope, sha
from backend.knowledge_bases.industrial_qa.config import add_runner_arguments, runner_config
from backend.knowledge_bases.industrial_qa.pipeline import build_retriever
from backend.knowledge_bases.industrial_qa.retrieval import (
    DOCUMENT_QUERY_INSTRUCTION, RERANK_MODES, IndustrialRetriever,
)
from backend.knowledge_bases.stores import open_document_store
from backend.src.agents.rag.cross_encoder import CrossEncoderScorer

INSTRUCTION = ("Given a question from a factory maintenance engineer, retrieve the expert knowledge record "
               "or equipment manual passage that answers it")
SOURCES = ("benchmark_adapter.py", "retrieval.py", "answering.py", "pipeline.py", "config.py", "rerank.md", "answer.md",
           "prefilter.py")
SOURCE_GROUPS = ("expert_only", "docs_only", "expert_and_docs")


def train_run(source: Path):
    run = json.loads((source / "run.json").read_text())
    if run["split"] != "train":
        raise ValueError("The prefilter experiment only reads the train split")
    if any(c.get("status") != "processed" for c in run["conversations"].values()):
        raise ValueError("Every train conversation must already be ingested")
    return run


class HybridOrder:
    """Control arm: keep the first N per source in hybrid order, no model."""
    model = "hybrid-order"
    provenance = {"model": model}

    async def score(self, query, texts):
        return [-position for position in range(len(texts))]


def scorer(args):
    if args.model == HybridOrder.model:
        return HybridOrder()
    return CrossEncoderScorer(args.model, batch_size=args.batch_size, max_length=args.max_length,
                              instruction=INSTRUCTION) if args.model else None


def candidate_items(source, by_evidence, conversations, question):
    if source.passage is not None:
        return [{"type": "document", "id": source.passage.doc_id}]
    cid = by_evidence.get(str(source.record.evidence_id))
    turns = pilot.record_turns(source.record, pilot.expert_turns(conversations[cid]), question) if cid else []
    return [{"type": "conversation", "id": cid, "turn_id": turn} for turn in turns]


def coverage(items, gold):
    def hit(item, unit):
        if unit["type"] == "document":
            return item["type"] == "document" and item["id"] == unit["id"]
        return item["type"] == "conversation" and item["id"] == unit["id"] and item["turn_id"] == unit["turn_id"]
    return sum(any(hit(item, unit) for item in items) for unit in gold) / len(gold) if gold else 0.0


def top(candidates, kind, limit, scores=None):
    labels = [label for label in candidates if label[0] == kind]
    if scores is not None:
        labels.sort(key=lambda label: -scores[label])
    return labels[:limit]


async def pool(args):
    run = train_run(args.from_run_dir)
    by_evidence = {c["evidence_id"]: cid for cid, c in run["conversations"].items()}
    conversations = pilot.public_conversations()
    gold = {q["question_id"]: q for q in pilot.gold_questions("train")}
    questions = pilot.public_questions("train")
    scorers = [CrossEncoderScorer(model, batch_size=args.batch_size, max_length=args.max_length,
                                  instruction=INSTRUCTION) for model in args.model]
    store = await pilot.KnowledgeStore.for_kb(SPEC, init_schema=False)
    documents = await open_document_store(SPEC, query_instruction=DOCUMENT_QUERY_INSTRUCTION, init_schema=False)
    retriever = IndustrialRetriever(None, prompt="")
    rows = []
    try:
        for qid in run["question_ids"]:
            question = questions[qid]["question"]
            _, _, candidates = await retriever.candidates(question, store, documents, scope=benchmark_scope(run))
            texts = {label: source.text.split(" ", 1)[1] for label, source in candidates.items()}
            row = {"question_id": qid, "knowledge_source": gold[qid]["knowledge_source"],
                   "difficulty": gold[qid]["difficulty"], "gold": gold[qid]["required_evidence"],
                   "characters": {label: len(source.text) for label, source in candidates.items()},
                   "items": {label: candidate_items(source, by_evidence, conversations, question)
                             for label, source in candidates.items()}, "scores": {}, "seconds": {}}
            for model in scorers:
                started = monotonic()
                values = await model.score(question, list(texts.values()))
                row["seconds"][model.model] = monotonic() - started
                row["scores"][model.model] = dict(zip(texts, values))
            rows.append(row)
            print(f"{qid}: {len(candidates)} candidates")
    finally:
        await store.aclose()
        await documents.aclose()
    pilot.save(args.run_dir / "pool.json", {"from_run_dir": str(args.from_run_dir.resolve()),
        "run_id": run["run_id"], "cross_encoders": [model.provenance for model in scorers], "questions": rows})
    summary = summarize_pool(rows, [model.model for model in scorers], args.keep)
    pilot.save(args.run_dir / "pool_summary.json", summary)
    print_pool(summary)
    return 0


def summarize_pool(rows, models, keeps):
    strategies = {"pool": lambda row, n: list(row["items"])}
    strategies["hybrid"] = lambda row, n: top(row["items"], "K", n) + top(row["items"], "D", n)
    for model in models:
        strategies[model] = lambda row, n, m=model: (top(row["items"], "K", n, row["scores"][m])
                                                     + top(row["items"], "D", n, row["scores"][m]))
    summary = {"questions": len(rows), "keep": keeps, "results": {}, "seconds": {
        model: {"median": statistics.median(r["seconds"][model] for r in rows),
                "max": max(r["seconds"][model] for r in rows)} for model in models}}
    for name, choose in strategies.items():
        for n in ([None] if name == "pool" else keeps):
            groups = defaultdict(list)
            for row in rows:
                labels = choose(row, n)
                value = coverage([item for label in labels for item in row["items"][label]], row["gold"])
                chars = sum(row["characters"][label] for label in labels)
                for group in ("overall", row["knowledge_source"]):
                    groups[group].append((value, len(labels), chars))
            summary["results"][name if n is None else f"{name}@{n}"] = {
                group: {"n": len(values), "gold_coverage": statistics.mean(v[0] for v in values),
                        "candidates": statistics.median(v[1] for v in values),
                        "characters": statistics.median(v[2] for v in values)}
                for group, values in groups.items()}
    return summary


def print_pool(summary):
    print(f"\n{'strategy':40} {'overall':>8} " + " ".join(f"{g:>16}" for g in SOURCE_GROUPS)
          + f" {'cand':>5} {'chars':>7}")
    for name, groups in summary["results"].items():
        print(f"{name:40} {groups['overall']['gold_coverage']:8.2%} "
              + " ".join(f"{groups[g]['gold_coverage']:16.2%}" for g in SOURCE_GROUPS)
              + f" {groups['overall']['candidates']:5.0f} {groups['overall']['characters']:7.0f}")
    for model, seconds in summary["seconds"].items():
        print(f"{model}: median {seconds['median']:.2f}s, max {seconds['max']:.2f}s per question")


class TimedRetrieval(ScopedRetrieval):
    """Wall-clock per question under the run's concurrency, so queueing is included."""

    def __init__(self, retriever, timings):
        super().__init__(retriever=retriever)
        self.timings = timings
        candidates, prefilter = retriever.candidates, retriever.prefilter

        async def timed_candidates(question, *args, **kwargs):
            started = monotonic()
            try:
                return await candidates(question, *args, **kwargs)
            finally:
                timings[question]["search_seconds"] = monotonic() - started
        retriever.candidates = timed_candidates
        if prefilter is not None:
            score = prefilter.score

            async def timed_score(question, texts):
                started = monotonic()
                try:
                    return await score(question, texts)
                finally:
                    timings[question]["prefilter_seconds"] = monotonic() - started
            prefilter.score = timed_score

    async def __call__(self, question, *args):
        started = monotonic()
        try:
            return await super().__call__(question, *args)
        finally:
            self.timings[question]["retrieval_seconds"] = monotonic() - started


def prepare(args):
    run = train_run(args.from_run_dir)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.from_run_dir / "run.json", args.run_dir / "run.json")
    args.question_ids = args.run_dir / "question_ids.txt"
    args.question_ids.write_text("\n".join(run["question_ids"]) + "\n")
    here = Path(__file__).parent
    cross_encoder = scorer(args)
    manifest = {
        "policy": "H1+H3" + (" with cross-encoder prefilter" if cross_encoder else "")
                  + (" with hybrid cut" if args.rerank_mode == "hybrid" else ""),
        "rerank_mode": args.rerank_mode,
        "hybrid_cut": {"knowledge": args.keep_knowledge, "manual": args.keep_manual}
                      if args.rerank_mode == "hybrid" else None,
        "database": SPEC.database, "split": "train", "run_id": run["run_id"],
        "reused_run_ids": run.get("reused_run_ids", []), "from_run_dir": str(args.from_run_dir.resolve()),
        "retrieval_scope": benchmark_scope(run), "k": args.k, "per_conversation": args.per_conversation,
        "context_characters": args.config.retrieval.context_characters, "concurrency": args.concurrency,
        "config": args.config.provenance(),
        "prefilter": cross_encoder and {**cross_encoder.provenance, "knowledge": args.keep_knowledge,
                                        "manual": args.keep_manual},
        "models": {key: os.getenv(key) for key in ("OPENROUTER_MODEL", "ANSWER_MODEL", "ANSWER_THINKING",
                                                   "EXTRACTION_MODEL", "MODELARK_MODELNAME", "EMBEDDING_MODEL")},
        "input_sha256": {str(path.relative_to(pilot.PROJECT_ROOT)): sha(path.read_text()) for path in
                         [pilot.PUBLIC_DIR / "corpus_chunks.jsonl", pilot.PUBLIC_DIR / "questions_train.jsonl"]},
        "source_sha256": {str(path.relative_to(pilot.PROJECT_ROOT)): sha(path.read_text()) for path in
                          [*(here / name for name in SOURCES), pilot.PROJECT_ROOT / "backend/src/agents/rag/cross_encoder.py"]},
    }
    path = args.run_dir / "manifest.json"
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError("Experiment configuration changed; use a new --run-dir")
    pilot.save(path, manifest)
    return run, cross_encoder


async def answer(args):
    run, cross_encoder = prepare(args)
    path = args.run_dir / "timings.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    timings = defaultdict(dict, saved)
    retrieval = TimedRetrieval(build_retriever(args.config, prefilter=cross_encoder), timings)
    try:
        code = await pilot.answer(args, run, retrieve=retrieval, generate=CompleteAnswer(args.config),
                                  init_schema=False, checkpoint_every=4)
    finally:
        pilot.save(path, dict(timings))
    if code:
        return code
    return pilot.evaluate(args, run, facts=False)


def run_row(directory: Path):
    results = json.loads((directory / "results.json").read_text())
    uncited = json.loads((directory / "results_uncited.json").read_text())
    answers = json.loads((directory / "answers.json").read_text())
    timings = json.loads((directory / "timings.json").read_text()) if (directory / "timings.json").exists() else {}
    traces = [next(iter(a["sources"].values()))["retrieval_trace"] for a in answers if a["sources"]]
    def median(values):
        values = [v for v in values if v is not None]
        return statistics.median(values) if values else None
    row = {"run": directory.name, "questions": len(answers)}
    for group in ("overall", *SOURCE_GROUPS):
        cited = results["overall"] if group == "overall" else results["by_knowledge_source"][group]
        before = uncited["overall"] if group == "overall" else uncited["by_knowledge_source"][group]
        row[group] = {"uncited_recall@5": before["recall@5"], "uncited_recall@10": before["recall@10"],
                      "uncited_cp@10": before["context_precision@10"], "cited_recall@5": cited["recall@5"],
                      "answer_correct": cited.get("answer_correct"), "answer_score": cited.get("answer_score"),
                      "mc_accuracy": cited["mc_accuracy"]}
    row["cost"] = {
        "ranked_candidates": median(t["candidate_count"] for t in traces),
        "rerank_input_tokens": median((t["ranker_usage"] or {}).get("input_tokens") for t in traces),
        "writer_input_tokens": median((a["answer_trace"]["usage"][0] or {}).get("input_tokens") for a in answers),
        "context_characters": median(a["context_characters"] for a in answers),
        "prefilter_seconds": median(t.get("prefilter_seconds") for t in timings.values()),
        "retrieval_seconds": median(t.get("retrieval_seconds") for t in timings.values()),
    }
    return row


def compare(args):
    rows = [run_row(directory) for directory in args.run_dirs]
    metrics = ("uncited_recall@5", "uncited_recall@10", "uncited_cp@10", "cited_recall@5",
               "answer_correct", "answer_score", "mc_accuracy")
    for group in ("overall", *SOURCE_GROUPS):
        print(f"\n## {group}")
        print(f"{'run':44}" + "".join(f"{m:>18}" for m in metrics))
        for row in rows:
            print(f"{row['run']:44}" + "".join(
                f"{row[group][m]:18.2%}" if row[group][m] is not None else f"{'-':>18}" for m in metrics))
    print("\n## cost (medians per question)")
    names = tuple(rows[0]["cost"])
    print(f"{'run':44}" + "".join(f"{name:>22}" for name in names))
    for row in rows:
        print(f"{row['run']:44}" + "".join(
            f"{row['cost'][n]:22.2f}" if row["cost"][n] is not None else f"{'-':>22}" for n in names))
    if args.out:
        pilot.save(args.out, rows)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("pool", "answer"):
        command = commands.add_parser(name)
        command.add_argument("--run-dir", type=Path, required=True)
        command.add_argument("--from-run-dir", type=Path, required=True)
        command.add_argument("--batch-size", type=int, default=8)
        command.add_argument("--max-length", type=int, default=1024)
    commands.choices["pool"].add_argument("--model", action="append", required=True)
    commands.choices["pool"].add_argument("--keep", type=lambda v: [int(x) for x in v.split(",")],
                                          default=[4, 6, 8, 10, 12])
    answer_parser = commands.choices["answer"]
    answer_parser.add_argument("--model", help=f"cross-encoder or {HybridOrder.model}; omit for the unchanged H1 baseline")
    add_runner_arguments(answer_parser)
    answer_parser.add_argument("--rerank-mode", choices=RERANK_MODES, help="override [retrieval] rerank_mode")
    answer_parser.add_argument("--keep-knowledge", type=int, help="override [retrieval] prefilter_knowledge")
    answer_parser.add_argument("--keep-manual", type=int, help="override [retrieval] prefilter_manual")
    answer_parser.add_argument("--concurrency", type=int, default=4)
    answer_parser.add_argument("--skip-llm", action="store_true", help="score retrieval and MC only")
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("run_dirs", type=Path, nargs="+")
    compare_parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.command == "compare":
        return compare(args)
    if args.command == "answer":
        try:
            config = runner_config(args)
            overrides = {"rerank_mode": args.rerank_mode, "prefilter_knowledge": args.keep_knowledge,
                         "prefilter_manual": args.keep_manual}
            args.config = config.with_retrieval(**{k: v for k, v in overrides.items() if v is not None})
        except ValueError as exc:
            parser.error(str(exc))
        retrieval = args.config.retrieval
        args.rerank_mode = retrieval.rerank_mode
        args.keep_knowledge, args.keep_manual = retrieval.prefilter_knowledge, retrieval.prefilter_manual
    load_dotenv(pilot.PROJECT_ROOT / ".env")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(pool(args) if args.command == "pool" else answer(args))


if __name__ == "__main__":
    raise SystemExit(main())
