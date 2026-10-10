"""Repair family scope without extraction/indexing, then evaluate the same train corpus.

Source artifacts stay immutable. Changed knowledge rows retain their IDs,
claims and evidence; only context, its search representation and scope revision
are updated. Manual chunks and all other records are fingerprint-checked.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import text

from backend.knowledge_bases.industrial_qa import CATALOG, SPEC, pilot
from backend.knowledge_bases.industrial_qa.experiment import CompleteAnswer, ScopedRetrieval, evaluate_combined, sha
from backend.knowledge_bases.industrial_qa.train import search_scope


def prepare(args):
    source = json.loads((args.from_run_dir / "run.json").read_text())
    previous_manifest = json.loads((args.from_run_dir / "manifest.json").read_text())
    if set(source["question_ids"]) != {q["question_id"] for q in pilot.gold_questions("train")}:
        raise ValueError("Family repair requires the completed full-train corpus")
    for path, digest in previous_manifest["input_sha256"].items():
        if sha((pilot.PROJECT_ROOT / path).read_text()) != digest:
            raise ValueError(f"Source inputs changed: {path}")
    pilot.SPEC = replace(SPEC, name=previous_manifest["database"].removeprefix("kb_"))
    run = json.loads(json.dumps(source))
    run["family_normalized_from"] = str(args.from_run_dir.resolve())
    old_rows, changes = {}, []
    public = pilot.public_conversations()
    for cid, conversation in run["conversations"].items():
        if conversation["status"] != "processed":
            raise ValueError("Source ingestion is incomplete")
        records = [pilot.KnowledgeRecord.model_validate(row) for row in conversation["records"]]
        original = {str(record.id): record.model_dump(mode="json") for record in records}
        CATALOG.normalize_records(pilot.render_transcript(public[cid]), records)
        for record in records:
            prior = original[str(record.id)]
            if record.context == prior["context"]:
                continue
            record.context, report = SPEC.ontology.normalize(record.context)
            record.revision = {**record.revision, "ontology": report.to_dict(), "family_normalization": {
                "old_context": prior["context"], "old_ontology": prior["revision"].get("ontology"),
                "old_catalog": prior["revision"].get("catalog"), "source_run": str(args.from_run_dir.resolve()),
                "schema_version": SPEC.extraction_version,
            }}
            old_rows[str(record.id)] = prior
            changes.append({"conversation_id": cid, "record_id": str(record.id),
                            "before": prior["context"], "after": record.context})
        conversation["records"] = [record.model_dump(mode="json") for record in records]
    args.run_dir.mkdir(parents=True, exist_ok=True)
    args.question_ids = args.run_dir / "question_ids.txt"
    args.question_ids.write_text("\n".join(run["question_ids"]) + "\n")
    paths = [Path(__file__), Path(pilot.__file__), Path(__file__).with_name("experiment.py"),
             Path(__file__).with_name("catalog.py"), Path(__file__).with_name("schema.py"),
             Path(__file__).with_name("extract.md"), Path(__file__).with_name("__init__.py"),
             Path(__file__).with_name("experiments") / "rerank.md",
             Path(__file__).with_name("experiments") / "answer_complete.md"]
    manifest = {"policy": "H1+H3 with canonical family and exact asset priority",
                "database": pilot.SPEC.database, "from_run_dir": str(args.from_run_dir.resolve()),
                "source_run_sha256": sha((args.from_run_dir / "run.json").read_text()),
                "source_manifest_sha256": sha((args.from_run_dir / "manifest.json").read_text()),
                "run_id": run["run_id"], "retrieval_scope": search_scope(run),
                "k": args.k, "per_conversation": args.per_conversation, "concurrency": args.concurrency,
                "question_ids": run["question_ids"], "conversation_ids": run["conversation_ids"],
                "changed_record_ids": sorted(old_rows),
                "input_sha256": previous_manifest["input_sha256"],
                "source_sha256": {str(p.relative_to(pilot.PROJECT_ROOT)): sha(p.read_text()) for p in paths},
                "models": previous_manifest["models"], "new_extraction": False, "manual_reindex": False}
    path = args.run_dir / "manifest.json"
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError("Repair configuration changed; use a new output directory")
    snapshot = args.run_dir / "snapshot"
    snapshot.mkdir(exist_ok=True)
    for path in paths:
        shutil.copyfile(path, snapshot / ("experiment_" + path.name if path.parent.name == "experiments" else path.name))
    pilot.save(args.run_dir / "manifest.json", manifest)
    pilot.save(args.run_dir / "run.json", run)
    pilot.save(args.run_dir / "scope_plan.json", changes)
    pilot.save(args.run_dir / "original_records.json", old_rows)
    print(f"Scope plan: {len(changes)} records changed; {len(run['conversation_ids'])} conversations, "
          f"{len(run['question_ids'])} questions reused")
    return run, old_rows


async def fingerprint(store, changed_ids):
    async with store.engine.connect() as conn:
        documents = dict((await conn.execute(text(
            "SELECT count(*) AS rows, md5(string_agg(md5(row_to_json(t)::text), '' ORDER BY id)) "
            "AS fingerprint FROM chunks_full t"
        ))).mappings().one())
        others = dict((await conn.execute(text(
            "SELECT count(*) AS rows, md5(string_agg(md5(row_to_json(t)::text), '' ORDER BY id)) "
            "AS fingerprint FROM knowledge_items t WHERE NOT (id = ANY(:ids))"
        ), {"ids": [UUID(key) for key in changed_ids]})).mappings().one())
        raw = dict((await conn.execute(text(
            "SELECT count(*) AS rows, md5(string_agg(md5(row_to_json(t)::text), '' ORDER BY id)) "
            "AS fingerprint FROM raw_conversations t"
        ))).mappings().one())
    return {"manual_chunks": documents, "other_records": others, "raw_conversations": raw}


async def apply(args, run, old_rows):
    store = await pilot.KnowledgeStore.for_kb(pilot.SPEC, init_schema=False)
    desired = {row["id"]: pilot.KnowledgeRecord.model_validate(row)
               for c in run["conversations"].values() for row in c["records"] if row["id"] in old_rows}
    try:
        before = await fingerprint(store, old_rows)
        path = args.run_dir / "inventory_before.json"
        if path.exists() and json.loads(path.read_text()) != before:
            raise ValueError("Unrelated database data changed")
        pilot.save(path, before)
        current = {str(row.id): row for row in await store.get_knowledge([UUID(key) for key in old_rows])}
        if set(current) != set(old_rows):
            raise ValueError("Scope repair records are missing")
        pending = []
        for key, record in current.items():
            old = old_rows[key]
            if any(record.model_dump(mode="json")[field] != old[field]
                   for field in ("claim", "subject", "type", "evidence_id")):
                raise ValueError("Record content changed since source checkpoint")
            if record.context == desired[key].context:
                continue  # a committed repair can resume without re-embedding
            if record.context != old["context"]:
                raise ValueError("Record context changed outside this repair")
            pending.append(desired[key])
        # Prove the lookup guard accepts the original inconsistent rows before
        # repairing them; this is a real SQL check independent of normalization.
        checks = []
        from backend.src.storage.knowledge_store import _compile_knowledge_filter
        async with store.engine.connect() as conn:
            for key in old_rows:
                asset = old_rows[key]["context"].get("machine_id")
                if asset in CATALOG.assets:
                    filters = {"$and": [search_scope(run), CATALOG.lookup_scope([asset], "wrong-family")]}
                    sql, parameters = _compile_knowledge_filter(filters)
                    found = await conn.scalar(text(f"SELECT count(*) FROM knowledge_items k WHERE k.id=:id AND ({sql})"),
                                              {**parameters, "id": UUID(key)})
                    if found != 1:
                        raise ValueError("Exact asset was excluded by the retrieval guard")
                    checks.append(key)
        count = await store.update_knowledge_scope(pending, revision_keys=("catalog", "ontology", "family_normalization"))
        if count != len(pending):
            raise ValueError("Scope update did not cover every pending record")
        updated = await store.get_knowledge([UUID(key) for key in old_rows])
        for record in updated:
            if record.context != desired[str(record.id)].context:
                raise ValueError("Scope update verification failed")
        for conversation in run["conversations"].values():
            by_id = {str(record.id): record for record in updated}
            conversation["records"] = [by_id[row["id"]].model_dump(mode="json") if row["id"] in by_id else row
                                       for row in conversation["records"]]
        after = await fingerprint(store, old_rows)
        if after != before:
            raise ValueError("Scope repair changed unrelated records, manuals or raw conversations")
        pilot.save(args.run_dir / "run.json", run)
        pilot.save(args.run_dir / "scope_applied.json", {
            "changed_records": len(old_rows), "updated_this_attempt": count,
            "exact_asset_guard_passed_ids": checks, "unrelated_inventory_unchanged": True,
            "inventory": after, "applied_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"Normalized {count} records; manual index, other records and raw conversations unchanged")
    finally:
        await store.aclose()


async def execute(args):
    run, old_rows = prepare(args)
    if args.command == "plan":
        return 0
    await apply(args, run, old_rows)
    if args.command in ("answer", "all"):
        baseline = json.loads((args.from_run_dir / "answers.json").read_text())
        budgets = {item["question"]: item["context_characters"] for item in baseline}
        if code := await pilot.answer(args, run, retrieve=ScopedRetrieval(budgets, scope=search_scope(run)),
                                     generate=CompleteAnswer(), init_schema=False, checkpoint_every=4):
            return code
    if args.command in ("evaluate", "all"):
        args.reuse_run_dir = args.from_run_dir
        if code := evaluate_combined(args, run):
            return code
        path = args.run_dir / "results.json"
        scores = json.loads(path.read_text())
        scores["ingestion_quality"] = pilot.ingestion_quality(run)
        scores["scope_repair"] = json.loads((args.run_dir / "scope_applied.json").read_text())
        pilot.save(path, scores)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply", "answer", "evaluate", "all"))
    parser.add_argument("--from-run-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--per-conversation", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--skip-llm", action="store_true")
    args = parser.parse_args()
    if min(args.k, args.concurrency) < 1 or args.per_conversation < 0:
        parser.error("Invalid retrieval settings")
    load_dotenv(pilot.PROJECT_ROOT / ".env")
    return asyncio.run(execute(args))


if __name__ == "__main__":
    raise SystemExit(main())
