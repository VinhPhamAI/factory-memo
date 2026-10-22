"""Benchmark ingestion: index the manual chunks and extract a run's conversations.

Extraction writes into the official database under the run ID; every other
run's records are replaced first (runs.overwrite_run), except the ones the
run explicitly reuses. Consolidation is scoped to the run's own records.
"""
from __future__ import annotations

import sys

from backend.knowledge_bases.industrial_qa import SPEC
from backend.knowledge_bases.industrial_qa.benchmark.data import (
    chunk_document, corpus_chunks, public_conversations, render_transcript, save,
)
from backend.knowledge_bases.industrial_qa.benchmark.runs import (
    overwrite_run, require_official_database, run_filter, unprocessed,
)
from backend.knowledge_bases.industrial_qa.retrieval import DOCUMENT_QUERY_INSTRUCTION
from backend.knowledge_bases.stores import open_document_store
from backend.scripts.kb import review_notes
from backend.src.agents.rag.indexing import index_documents
from backend.src.services.knowledge_ingestion import IngestionInput, IngestionResult, KnowledgeIngestionService
from backend.src.storage import KnowledgeStore


async def ingest(run, run_dir, config, *, index_manuals_first=False, init_schema=True, **options) -> int:
    """Replace other runs in the official database, then extract what is missing.

    `options` are those of extract_conversations; with index_manuals_first,
    every manual chunk is indexed again before extraction.
    """
    store = await KnowledgeStore.for_kb(require_official_database(SPEC), init_schema=init_schema)
    try:
        async with overwrite_run(store, run) as report:
            save(run_dir / "corpus_replacement.json", report)
            if index_manuals_first:
                await index_manuals()
            return await extract_conversations(run, run_dir, config, init_schema=init_schema, **options)
    finally:
        await store.aclose()


async def index_manuals() -> None:
    """Index every public manual chunk as a document (embeddings only)."""
    documents = await open_document_store(SPEC, query_instruction=DOCUMENT_QUERY_INSTRUCTION)
    try:
        await index_documents(documents, [chunk_document(c) for c in corpus_chunks().values()], SPEC.chunking)
    finally:
        await documents.aclose()


async def extract_conversations(
    run, run_dir, config, *, verify, concurrency, consolidate, batch_size=None, extractor=None, init_schema=True,
) -> int:
    """Extract the run's unprocessed conversations, saving run.json after each batch.

    Returns 1 when a conversation is still not extracted.
    """
    remaining = unprocessed(run)
    if not remaining:
        print("All conversations of the run already extracted")
        return 0
    conversations = public_conversations()
    extractor = extractor or config.extractor(SPEC)
    run["extraction"] = extractor.provenance
    store = await KnowledgeStore.for_kb(require_official_database(SPEC), init_schema=init_schema)
    try:
        # Scoped: a run must not merge into, or link, other runs' records,
        # including the ones it reuses.
        service = KnowledgeIngestionService(
            extractor, store,
            extraction_metadata={"version": SPEC.extraction_version, **extractor.provenance},
            ontology=SPEC.ontology,
            normalizer=SPEC.record_normalizer,
            verifier=config.verifier(verify),
            consolidator=config.consolidator(store, scope=run_filter(run)) if consolidate else None,
        )
        size = batch_size or len(remaining)
        for start in range(0, len(remaining), size):
            batch = remaining[start:start + size]
            results = await service.ingest_batch(
                [
                    IngestionInput(
                        render_transcript(conversations[cid]),
                        metadata={"kb": SPEC.name, "conversation_id": cid, "benchmark_run_id": run["run_id"]},
                        context={"benchmark_run_id": run["run_id"]},
                    )
                    for cid in batch
                ],
                concurrency=concurrency,
            )
            for cid, result in zip(batch, results, strict=True):
                run["conversations"][cid] = _outcome(cid, result)
            save(run_dir / "run.json", run)
            if batch_size:
                done = len(run["conversation_ids"]) - len(unprocessed(run))
                print(f"Checkpoint: {done}/{len(run['conversation_ids'])} conversations processed")
    finally:
        await store.aclose()
    return int(bool(unprocessed(run)))


def _outcome(cid, result):
    if not isinstance(result, IngestionResult):
        print(f"{cid}: FAILED {type(result).__name__}: {result}", file=sys.stderr)
        return {"status": "failed", "error": str(result)}
    actions = ", ".join(f"{n} {name}" for name, n in result.consolidation.items())
    if errors := sum("error" in r.revision.get("consolidation", {}) for r in result.records):
        actions += f"; consolidation failed for {errors}"
    print(f"{cid}: {len(result.records)} records" + (f" ({actions})" if actions else ""))
    for note in review_notes((*result.records, *result.merged)):
        print(f"  {note}")
    return {
        "status": "processed",
        "evidence_id": str(result.evidence_id),
        "records": [record.model_dump(mode="json") for record in result.records],
        "merged": [record.model_dump(mode="json") for record in result.merged],
        "consolidation": result.consolidation,
    }
