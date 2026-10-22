"""Score a run with benchmark/evaluate.py, plus record recall and ingestion metrics.

The answer and fact judge is MODELARK_*, else the EXTRACTION_* / OPENROUTER_*
model; without one only retrieval and MC are scored.
"""
from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from backend.knowledge_bases.base import PROJECT_ROOT
from backend.knowledge_bases.industrial_qa.benchmark.data import (
    BENCHMARK_DIR, expert_turns, gold_conversations, gold_questions, public_conversations, save,
)
from backend.knowledge_bases.industrial_qa.benchmark.evidence import covered_facts, tokens
from backend.knowledge_bases.industrial_qa.benchmark.runs import extracted
from backend.knowledge_bases.industrial_qa.retrieval import UNSCOPED_CONTEXT
from backend.src.services import llm
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage import KnowledgeRecord


def judge_env() -> dict[str, str] | None:
    """Environment for benchmark/evaluate.py's judge, None when no model is set.

    MODELARK_* wins; otherwise the judge is the EXTRACTION_* (else
    OPENROUTER_*) model, which may also be the model under test.
    """

    def setting(name: str) -> str | None:
        return os.getenv(f"EXTRACTION_{name}") or os.getenv(f"OPENROUTER_{name}")

    judge = {
        "MODELARK_BASEURL": os.getenv("MODELARK_BASEURL")
        or setting("BASE_URL") or llm.DEFAULT_LLM_BASE_URL,
        "MODELARK_APIKEY": os.getenv("MODELARK_APIKEY") or setting("API_KEY"),
        "MODELARK_MODELNAME": os.getenv("MODELARK_MODELNAME") or setting("MODEL"),
    }
    if not all(judge.values()):
        return None
    return {**os.environ, **judge}  # type: ignore[dict-item]


def run_evaluator(
    args, run: dict[str, Any], predictions: str, out: Path,
    env: dict[str, str] | None, *, facts: bool = True,
) -> int:
    command = [
        sys.executable, str(BENCHMARK_DIR / "evaluate.py"),
        "--predictions", str(args.run_dir / predictions),
        "--split", run["split"],
        "--question-ids", str(args.run_dir / "question_ids.txt"),
        "--k", "1,3,5,10",
        "--out", str(out),
    ]
    if env is None:
        command.append("--skip-llm")
    elif facts:
        command.append("--facts")
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False, env=env).returncode


def evaluate(args, run: dict[str, Any], *, facts: bool = True) -> int:
    """facts=False judges answers only, for runs whose extraction did not change."""
    results = args.run_dir / "results.json"
    env = None if args.skip_llm else judge_env()
    if env is None and not args.skip_llm:
        print("No judge model is configured: scoring retrieval and MC only")
    elif env is not None:
        print(f"Judge: {env['MODELARK_MODELNAME']}")
    if code := run_evaluator(args, run, "predictions.jsonl", results, env, facts=facts):
        return code
    scores = json.loads(results.read_text(encoding="utf-8"))
    uncited = args.run_dir / "results_uncited.json"
    if (args.run_dir / "predictions_uncited.jsonl").exists():
        # Retrieval before the citation rerank; the answers are the same.
        if code := run_evaluator(args, run, "predictions_uncited.jsonl", uncited, None):
            return code
        scores["uncited_retrieval"] = json.loads(uncited.read_text(encoding="utf-8"))["overall"]
    answers = json.loads((args.run_dir / "answers.json").read_text(encoding="utf-8"))
    if answers:
        scores["record_recall"] = record_recall(answers, run["split"])
        print("Record recall: " + " | ".join(
            f"{name} {value:.3f}" for name, value in scores["record_recall"].get("overall", {}).items()
        ))
    scores["extraction_turn_coverage"] = extraction_coverage(run)
    scores["ingestion_quality"] = ingestion_quality(run)
    if answers:
        scores["retrieval_diversity"] = retrieval_diversity(answers)
    save(results, scores)
    coverage = scores["extraction_turn_coverage"]
    print(f"Extraction: {coverage['records']} records; expert facts stated at a turn a record "
          f"points to: {coverage['covered']}/{coverage['expert_facts']}")
    print("Ingestion: " + json.dumps(scores["ingestion_quality"]["summary"]))
    return 0


def record_recall(
    answers: list[dict[str, Any]], split: str, ks: tuple[int, ...] = (1, 3, 5, 10)
) -> dict[str, Any]:
    """Share of a question's gold evidence among the turns of its top k records.

    Unlike the turn ranking, a record counts as one result whatever the number
    of turns it points to.
    """

    gold = {q["question_id"]: q for q in gold_questions(split)}
    rows: dict[str, list[dict[str, float]]] = defaultdict(list)
    for answer_ in answers:
        question = gold[answer_["question_id"]]
        evidence = question["required_evidence"]
        records = [s for label, s in answer_["sources"].items() if label.startswith("K")]
        row = {}
        for k in ks:
            pointed = {(item["id"], item.get("turn_id")) for s in records[:k] for item in s["items"]}
            hits = sum((item["id"], item.get("turn_id")) in pointed for item in evidence)
            row[f"recall@{k}"] = hits / len(evidence) if evidence else 0.0
        rows["overall"].append(row)
        rows[question["knowledge_source"]].append(row)
    return {
        group: {name: sum(r[name] for r in group_rows) / len(group_rows) for name in group_rows[0]}
        for group, group_rows in rows.items()
    }


def extraction_coverage(run: dict[str, Any]) -> dict[str, Any]:
    """Gold expert facts stated at a turn some extracted record points to."""

    turns = {cid: expert_turns(c) for cid, c in public_conversations().items()}
    fact_turns = {cid: c["fact_turns"] for cid, c in gold_conversations().items()}
    per_conversation = {}
    for cid, conversation in run["conversations"].items():
        if conversation.get("status") != "processed":
            continue
        records = extracted(conversation)
        per_conversation[cid] = {
            "records": len(records),
            "covered": covered_facts(records, turns[cid], fact_turns[cid]),
            "expert_facts": len(fact_turns[cid]),
        }
    return {
        "records": sum(c["records"] for c in per_conversation.values()),
        "covered": sum(len(c["covered"]) for c in per_conversation.values()),
        "expert_facts": sum(c["expert_facts"] for c in per_conversation.values()),
        "per_conversation": per_conversation,
    }


def ingestion_quality(run: dict[str, Any]) -> dict[str, Any]:
    """Verification, scope, case/lesson overlap and consolidation of the records."""

    records = [
        record for conversation in run["conversations"].values()
        if conversation.get("status") == "processed" for record in extracted(conversation)
    ]
    by_conversation: dict[str, list[KnowledgeRecord]] = defaultdict(list)
    for record in records:
        by_conversation[str(record.evidence_id)].append(record)
    overlaps = []
    for group in by_conversation.values():
        case_words = set().union(*(
            tokens(record.claim) for record in group if record.type is KnowledgeType.CASE
        ))
        for record in group:
            if record.type is not KnowledgeType.CASE and (words := tokens(record.claim)):
                overlaps.append(len(words & case_words) / len(words))
    lessons = [record for record in records if record.type is not KnowledgeType.CASE]
    consolidation = [record.revision.get("consolidation") or {} for record in lessons]
    types: dict[str, int] = defaultdict(int)
    for record in records:
        types[record.type.value] += 1
    summary = {
        "records": len(records),
        "verify_passed": sum(bool((r.revision.get("verify") or {}).get("passed")) for r in records),
        "scoped": sum(any(k not in UNSCOPED_CONTEXT for k in r.context) for r in records),
        "lessons_with_source_turns": sum(bool(r.context.get("source_turns")) for r in lessons),
        "lesson_case_overlap": round(sum(overlaps) / len(overlaps), 3) if overlaps else 0.0,
        "lessons_mostly_in_case": sum(share >= 0.8 for share in overlaps),
        "consolidation_errors": sum("error" in note for note in consolidation),
        "with_machine_id": sum(bool(r.context.get("machine_id")) for r in records),
        "catalog_changed": sum(bool(r.revision.get("catalog")) for r in records),
        "catalog_inherited": sum(bool((r.revision.get("catalog") or {}).get("inherited")) for r in records),
        "ontology_unknown": sum(bool((r.revision.get("ontology") or {}).get("unknown")) for r in records),
    }
    actions: dict[str, int] = defaultdict(int)
    for note in consolidation:
        actions[note.get("action", "none")] += 1
    return {"summary": summary, "types": dict(types), "consolidation_actions": dict(actions)}


def retrieval_diversity(answers: list[dict[str, Any]], k: int = 10) -> dict[str, float]:
    """Mean distinct conversations among a question's top k records."""

    distinct = [
        len({s["conversation_id"] for label, s in a["sources"].items() if label.startswith("K")
             and int(label[1:]) <= k})
        for a in answers
    ]
    return {f"conversations@{k}": round(sum(distinct) / len(distinct), 3) if distinct else 0.0}
