"""Rescope a run's stored records with the current equipment catalog and ontology.

Extraction, verification and consolidation are not repeated, so a catalog
change is measured alone. The records belong to the run id and are updated
in place: the source run keeps its result files but not its database state.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import shutil
import sys
from typing import Any
from uuid import UUID

from backend.knowledge_bases.industrial_qa import CATALOG, SPEC
from backend.knowledge_bases.industrial_qa.benchmark.data import public_conversations, render_transcript, save
from backend.knowledge_bases.industrial_qa.benchmark.runs import require_official_database
from backend.src.storage import KnowledgeRecord, KnowledgeStore


# Revision keys a re-normalization rewrites; links and history are kept.
SCOPE_REVISION_KEYS = ("catalog", "ontology")


def normalize_scope(transcript: str, records: list[KnowledgeRecord]) -> None:
    """The scope steps of ingestion: the record normalizer, then the ontology.

    The new ontology report keeps the spellings normalized before and replaces
    what still needs review.
    """

    if SPEC.record_normalizer is not None:
        SPEC.record_normalizer(transcript, records)
    if SPEC.ontology is None:
        return
    for record in records:
        record.context, report = SPEC.ontology.normalize(record.context)
        old = record.revision.get("ontology") or {}
        merged = {
            section: {**old.get(section, {}), **new}
            for section, new in report.to_dict().items()
        }
        for section in ("normalized", "implied"):
            if old.get(section) and section not in merged:
                merged[section] = old[section]
        if merged != old:
            record.revision = {**record.revision, "ontology": merged}


def start_renormalized_run(args) -> None:
    """Copy the sample of --from-run-dir into a new --run-dir."""

    if args.from_run_dir is None:
        raise SystemExit("renormalize needs --from-run-dir, the run whose records to rescope")
    if (args.run_dir / "run.json").exists():
        return  # resumed
    source = args.from_run_dir / "run.json"
    if not source.is_file():
        raise SystemExit(f"No run.json in {args.from_run_dir}")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    run = json.loads(source.read_text(encoding="utf-8"))
    run["renormalized_from"] = str(args.from_run_dir)
    save(args.run_dir / "run.json", run)
    if (args.from_run_dir / "question_ids.txt").is_file():
        shutil.copy(args.from_run_dir / "question_ids.txt", args.run_dir / "question_ids.txt")


async def renormalize(args, run: dict[str, Any]) -> int:
    """Rescope the run's stored records with the current catalog and ontology.

    Updates the records in place (context, search text, embedding and the
    catalog/ontology revision) and mirrors them in run.json; extraction,
    verification and consolidation are not repeated. The records belong to
    the run id, so --from-run-dir keeps its result files but its database
    records change too.
    """

    conversations = public_conversations()
    keys = SPEC.embedding_context_keys
    totals: dict[str, int] = defaultdict(int)
    store = await KnowledgeStore.for_kb(require_official_database(SPEC))
    try:
        for cid, conversation in run["conversations"].items():
            if conversation.get("status") != "processed":
                continue
            rows = [*conversation["records"], *conversation.get("merged", [])]
            ids = [UUID(row["id"]) for row in conversation["records"]]
            stored = await store.get_knowledge(ids)
            if missing := len(ids) - len(stored):
                print(f"{cid}: {missing} records not in the database", file=sys.stderr)
            stored_ids = {record.id for record in stored}
            # Merged records are not stored but count in ingestion quality.
            group = [*stored, *(
                KnowledgeRecord.model_validate(row) for row in conversation.get("merged", [])
                if UUID(row["id"]) not in stored_ids
            )]
            before = {
                record.id: (dict(record.context), record.embedding_text(keys))
                for record in group
            }
            normalize_scope(render_transcript(conversations[cid]), group)
            changed = [
                record for record in group
                if (record.context, record.embedding_text(keys)) != before[record.id]
            ]
            updated = await store.update_knowledge_scope(
                [record for record in changed if record.id in stored_ids],
                revision_keys=SCOPE_REVISION_KEYS,
            )
            by_id = {str(record.id): record for record in group}
            for row in rows:
                if (record := by_id.get(row["id"])) is not None:
                    row["context"] = record.context
                    row["revision"] = {**row.get("revision", {}), **{
                        key: record.revision[key]
                        for key in SCOPE_REVISION_KEYS if key in record.revision
                    }}
            totals["records"] += len(group)
            totals["changed"] += len(changed)
            totals["updated"] += updated
            totals["with_machine_id"] += sum(bool(r.context.get("machine_id")) for r in group)
            print(f"{cid}: {len(changed)}/{len(group)} records rescoped, {updated} updated")
    finally:
        await store.aclose()
    run["renormalized"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "catalog_sha256": CATALOG.sha256,
        "ontology_sha256": SPEC.ontology.sha256 if SPEC.ontology else None,
        **totals,
    }
    save(args.run_dir / "run.json", run)
    print("Renormalized: " + json.dumps(dict(totals)))
    return 0
