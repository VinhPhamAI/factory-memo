"""Answer a run's questions and write predictions.jsonl for benchmark/evaluate.py."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from backend.knowledge_bases.industrial_qa import SPEC
from backend.knowledge_bases.industrial_qa.benchmark.adapter import CompleteAnswer, ScopedRetrieval
from backend.knowledge_bases.industrial_qa.benchmark.data import (
    corpus_chunks, public_conversations, public_questions, save, write_jsonl,
)
from backend.knowledge_bases.industrial_qa.benchmark.evidence import cited_ranking
from backend.knowledge_bases.industrial_qa.benchmark.runs import extracted, require_official_database
from backend.knowledge_bases.industrial_qa.config import config_from
from backend.knowledge_bases.industrial_qa.retrieval import DOCUMENT_QUERY_INSTRUCTION
from backend.knowledge_bases.stores import open_document_store
from backend.src.storage import KnowledgeStore


async def answer(
    args, run: dict[str, Any], *, retrieve=None, generate=None, init_schema=True, checkpoint_every=0,
) -> int:
    """Answer the run's questions; `retrieve` and `generate` default to the official pipeline.

    The evidence ranking puts the sources the answer cites first, in citation
    order; predictions_uncited.jsonl keeps the ranking before that. Answers
    of an earlier attempt are kept; only missing questions are asked.
    """
    if retrieve is None or generate is None:
        config = config_from(args)
        retrieve = retrieve if retrieve is not None else ScopedRetrieval(config=config)
        generate = generate if generate is not None else CompleteAnswer(config)
    processed = {cid: c for cid, c in run["conversations"].items() if c["status"] == "processed"}
    if missing := [cid for cid in run["conversation_ids"] if cid not in processed]:
        print(f"Warning: {len(missing)} conversations not extracted: {', '.join(missing)}",
              file=sys.stderr)
    by_evidence = {c["evidence_id"]: cid for cid, c in processed.items()}
    conversations = public_conversations()
    chunks = corpus_chunks()
    questions = public_questions(run["split"])
    store = await KnowledgeStore.for_kb(require_official_database(SPEC), init_schema=init_schema)
    documents = await open_document_store(
        SPEC, query_instruction=DOCUMENT_QUERY_INSTRUCTION, init_schema=init_schema
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    async def solve(qid: str) -> dict[str, Any]:
        question = questions[qid]
        async with semaphore:
            sources, retrieved = await retrieve(
                question["question"], store, documents, run, args, by_evidence, conversations, chunks
            )
            output, trace = await generate(question, sources)
        print(f"{qid}: {output.choice} ({len(sources)} sources)")
        cited = list(dict.fromkeys(label for label in output.sources if label in sources))
        outcome = {
            "question_id": qid,
            "question": question["question"],
            "answer": output.answer,
            "choice": output.choice,
            "cited": cited,
            "retrieved": cited_ranking(sources, cited, retrieved),
            "retrieved_uncited": retrieved,
            "sources": {label: {k: v for k, v in s.items() if k != "text"}
                        for label, s in sources.items()},
            "context_characters": sum(len(s["text"]) for s in sources.values()),
            "answer_trace": trace,
        }
        if checkpoint_every:
            done[qid] = outcome
            if len(done) % checkpoint_every == 0:
                save(path, [done[q] for q in run["question_ids"] if q in done])
        return outcome

    # Answers of an earlier attempt are kept; only missing questions are asked.
    path = args.run_dir / "answers.json"
    done = {a["question_id"]: a for a in json.loads(path.read_text())} if path.exists() else {}
    remaining = [qid for qid in run["question_ids"] if qid not in done]
    try:
        outcomes = await asyncio.gather(*(solve(qid) for qid in remaining), return_exceptions=True)
    finally:
        await store.aclose()
        await documents.aclose()
    for qid, outcome in zip(remaining, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            print(f"{qid}: FAILED {type(outcome).__name__}: {outcome}", file=sys.stderr)
        else:
            done[qid] = outcome
    answers = [done[qid] for qid in run["question_ids"] if qid in done]
    save(path, answers)
    extractions = [
        # Every extracted record is one fact; case claims render the whole card.
        {"type": "extraction", "conversation_id": cid,
         "facts": [record.claim for record in extracted(processed[cid])]}
        for cid in run["conversation_ids"] if cid in processed
    ]
    for name, ranking in (("predictions", "retrieved"), ("predictions_uncited", "retrieved_uncited")):
        write_jsonl(args.run_dir / f"{name}.jsonl", [
            {"type": "answer", "question_id": a["question_id"], "answer": a["answer"],
             "choice": a["choice"], "retrieved": a.get(ranking, a["retrieved"])}
            for a in answers
        ] + extractions)
    print(f"Answered {len(answers)}/{len(run['question_ids'])}; wrote "
          f"{args.run_dir / 'predictions.jsonl'}")
    return 0 if len(answers) == len(run["question_ids"]) else 1
