"""Summarize completed ablations from local artifacts, without model/DB calls."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

from backend.knowledge_bases.industrial_qa import pilot


def avg(values):
    return statistics.mean(values) if values else 0


def collect(directory, gold):
    run = json.loads((directory / "run.json").read_text())
    result = json.loads((directory / "results.json").read_text())
    answers = json.loads((directory / "answers.json").read_text())
    rows = {r["question_id"]: r for r in result["per_question"]}
    uncited = json.loads((directory / "results_uncited.json").read_text())
    records = [r for c in run["conversations"].values() for r in c.get("records", [])]
    wrong_asset = []
    support_coverage = []
    for answer in answers:
        question = gold[answer["question_id"]]
        sources = answer["sources"]
        items = [item for source in sources.values() for item in source["items"]]
        evidence = question["required_evidence"]
        support_coverage.append(avg([float(any(
            item["type"] == unit["type"] and item["id"] == unit["id"]
            and (unit["type"] != "conversation" or item.get("turn_id") == unit["turn_id"])
            for item in items
        )) for unit in evidence]))
        knowledge = [s for label, s in sources.items() if label.startswith("K")]
        if question["knowledge_source"] != "docs_only" and knowledge:
            wrong_asset.append(avg([float(s["conversation_id"] != question["conversation_id"]) for s in knowledge]))
    return {
        "name": directory.name, "run_id": run["run_id"],
        "questions": len(run["question_ids"]), "question_ids": run["question_ids"],
        "conversations": len(set(run["conversation_ids"])),
        "statuses": dict(Counter(c["status"] for c in run["conversations"].values())),
        "records": len(records), "types": dict(Counter(r["type"] for r in records)),
        "overall": result["overall"], "uncited": uncited["overall"],
        "by_source": result["by_knowledge_source"], "by_difficulty": result["by_difficulty"],
        "fact_capture": result.get("fact_capture"), "ingestion_quality": result.get("ingestion_quality"),
        "mean_context_characters": avg([a["context_characters"] for a in answers]),
        "mean_sources": avg([len(a["sources"]) for a in answers]),
        "mean_answer_characters": avg([len(a["answer"]) for a in answers]),
        "foreign_conversation_knowledge_share": avg(wrong_asset),
        "source_pointer_gold_coverage": avg(support_coverage),
        "contradictions": [r["question_id"] for r in rows.values() if (r.get("judge") or {}).get("contradiction")],
        "incorrect_questions": [r["question_id"] for r in rows.values() if not r.get("answer_correct")],
        "per_question": rows,
    }


def compare(base, candidate):
    deltas = []
    for qid in base["question_ids"]:
        a, b = base["per_question"][qid], candidate["per_question"][qid]
        if a["answer_correct"] != b["answer_correct"] or a["answer_score"] != b["answer_score"]:
            deltas.append({"question_id": qid, "source": a["knowledge_source"],
                           "before_correct": a["answer_correct"], "after_correct": b["answer_correct"],
                           "before_score": a["answer_score"], "after_score": b["answer_score"],
                           "judge_reason": (b.get("judge") or {}).get("reason")})
    return deltas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    names = ["baseline", "retrieval", "extraction", "answer", "paired-retrieval", "paired-answer"]
    missing = [name for name in names if not (args.group_dir / name / "results.json").exists()]
    if args.strict and missing:
        raise SystemExit(f"Incomplete runs: {missing}")
    gold = {q["question_id"]: q for q in pilot.gold_questions("train")}
    runs = [collect(args.group_dir / name, gold) for name in names if name not in missing]
    baseline = next((r for r in runs if r["name"] == "baseline"), None)
    if baseline:
        for run in runs:
            if run["question_ids"] != baseline["question_ids"] or run["questions"] != 20:
                raise ValueError("Samples differ")
            if run["statuses"] != {"processed": 20}:
                raise ValueError(f"Incomplete ingestion: {run['name']}")
            run["answer_changes_vs_baseline"] = compare(baseline, run)
    output = {"missing": missing, "runs": runs}
    pilot.save(args.group_dir / "comparison.json", output)
    lines = [
        "# Industrial QA train20 ablations — 10/10/2026", "",
        "Các con số bên dưới được đọc từ artifact của các run đã hoàn thành. "
        "Mỗi fresh run ingest lại đúng 20 conversations; paired runs dùng lại extraction của baseline. "
        "Toàn bộ 1.067 manual chunks giữ nguyên trong database thử nghiệm riêng. Không mở rộng raw corpus.", "",
        "| Run | Uncited R@5 | Uncited R@10 | Uncited CP@10 | Cited R@5 | MC | Free-text correct | Answer score |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    pct = lambda value: f"{value * 100:.2f}%"
    for run in runs:
        a, u = run["overall"], run["uncited"]
        values = [u["recall@5"], u["recall@10"], u["context_precision@10"],
                  a["recall@5"], a["mc_accuracy"], a["answer_correct"], a["answer_score"]]
        lines.append(f"| {run['name']} | " + " | ".join(map(pct, values)) + " |")
    lines += ["", "**Chi phí context và ingestion**", "",
              "| Run | Records | Sources/câu | Context ký tự/câu | Answer ký tự/câu | Knowledge từ conversation khác | Fact P/R |",
              "|---|---:|---:|---:|---:|---:|---|",]
    for run in runs:
        f = run.get("fact_capture") or {}
        lines.append(f"| {run['name']} | {run['records']} | {run['mean_sources']:.1f} | "
                     f"{run['mean_context_characters']:.0f} | {run['mean_answer_characters']:.0f} | "
                     f"{pct(run['foreign_conversation_knowledge_share'])} | "
                     f"{pct(f.get('precision', 0))} / {pct(f.get('recall', 0))} |")
    interpretation = args.group_dir / "interpretation.md"
    if not missing and interpretation.exists():
        lines += ["", interpretation.read_text().strip(), ""]
    for run in runs:
        lines += ["", f"**{run['name']}**", "",
                  f"Run ID: `{run['run_id']}`. Contradiction flags: {run['contradictions'] or 'none'}. "
                  f"Câu chưa đạt free-text correctness: {run['incorrect_questions'] or 'none'}.", ""]
        for delta in run.get("answer_changes_vs_baseline", []):
            lines.append(f"- {delta['question_id']}: correctness {delta['before_correct']:.0f} → "
                         f"{delta['after_correct']:.0f}; score {delta['before_score']:.3f} → "
                         f"{delta['after_score']:.3f}. {delta['judge_reason']}")
    lines += ["", "**Giới hạn diễn giải**", "",
              "Đây là mẫu train 20 câu, chưa có distractor corpus lớn; một câu tương đương 5 điểm phần trăm accuracy. "
              "Fresh extraction có variance, vì vậy paired-retrieval và paired-answer là đối chứng quan trọng. "
              "Judge dùng cùng model family với extraction/answer; verdict không phải đánh giá của chuyên gia. "
              "Uncited ranking được tạo trước answer; cited ranking đã chịu tác động của answer/citation. "
              "Context Precision là mean precision tại các rank relevant, không phải số relevant chia k. "
              "Source-pointer coverage không chứng minh claim giữ đủ nội dung của turn. "
              "Giới hạn context của retrieval dùng số ký tự baseline, không phải token tokenizer chính xác.", "",
              "Manifest, source snapshots, audit outputs, answers, predictions và judge verdicts nằm trong từng thư mục run. "
              "Source-only provenance repair khi resume được ghi trong manifest_revisions.json; failed raw attempts được giữ để audit.", ""]
    (args.group_dir / "report.md").write_text("\n".join(lines))
    print(args.group_dir / "report.md")


if __name__ == "__main__":
    main()
