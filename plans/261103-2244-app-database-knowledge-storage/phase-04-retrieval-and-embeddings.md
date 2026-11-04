# Phase 4 — Retrieval and embeddings

Depends on phases 1–3 and decision D3.

Status: implemented. Code: `storage/embeddings.py` (checks),
`storage/chunk_retrieval.py` (agent store), `ChunkRepository.search_*`,
`knowledge_bases/stores.py:open_application_store` and `KnowledgeAgentPool`. Tests:
`backend/tests/test_chunk_retrieval.py`. The API serves catalog KBs from
`knowledge_chunks`; Industrial QA keeps its legacy stores.

## Embeddings

- At API and worker startup, read the size of `knowledge_chunks.embedding`
  (`pg_attribute.atttypmod`, as revision `3665f6870fa4` does) and compare it with
  `EMBEDDING_DIM`. On mismatch, refuse to serve ingestion or search with a message
  naming both sizes and the re-embedding step.
- Record `embedding_model` and `embedding_dim` in `extraction_runs.settings` for both
  modes, so the fingerprint changes with the model. Before search or ingestion in a KB,
  compare `EMBEDDING_MODEL` with the model of its done runs; on mismatch, return a
  clear error instead of mixing vectors.
- Chunks with a `NULL` embedding (the trigger clears it when `search_text` changes)
  are excluded from dense search and still found by keyword search. Scope edits
  (`update_knowledge_scope`) re-embed in the same transaction.

## Search (`backend/src/storage/repositories/chunks.py`)

- Port `search_knowledge`, `similar_knowledge`, `lexical_knowledge` and the document
  search to `knowledge_chunks`: cosine distance on `embedding`, keyword rank on
  `search_vector` (D3), reciprocal rank fusion and the existing minimum similarity.
- Apply the same inclusion filter to dense and keyword candidates:
  - chunk `knowledge_base_id` matches, `deleted_at IS NULL`, `status <> 'deprecated'`;
  - its KB is not deleted;
  - at least one linked source and its file (if any) are not deleted;
  - `chunk_kind` matches the tool (`knowledge` for `search_knowledge`,
    `document_passage` for document search).
- Translate context filters to `details->'context'->>'<key>'`. Keep
  `build_knowledge_filter` semantics, including unscoped matches.
- Return the existing hit types (`KnowledgeSearchHit`, `ChunkHit`) so
  `agents/agent/tools.py`, citation validation and the Industrial QA pipeline do not
  change. `[K:<id>]` uses the chunk ID; `[D:<id>]` uses `details.doc_id`.

## Callers

- `backend/knowledge_bases/stores.py`: return repository-backed stores for served KBs.
- `backend/api/knowledge_agent.py` (`KnowledgeAgentPool`): open stores with the shared
  engine; keep stream events and trace allowlists unchanged.
- `backend/knowledge_bases/industrial_qa/`: unchanged. Its CLI and benchmarks keep the
  legacy stores; the API no longer serves it.

## First failing tests

- Search never returns deprecated, soft-deleted, other-KB or deleted-source chunks,
  in dense, keyword or hybrid mode.
- A draft chunk is returned with its status.
- An embedding size different from `EMBEDDING_DIM` fails startup checks with both sizes
  in the message; a different `EMBEDDING_MODEL` fails search with a clear error.
- `test_knowledge_agent_api.py`: citations to retrieved chunk IDs validate; a
  citation to an unretrieved ID is rejected.

## Completion checks

- In a running stack, ask a question in a created KB with knowledge records and
  document passages; the answer cites retrieved chunks and each citation opens its source.
- `uv run python -m unittest backend.tests.test_knowledge_agent -v` passes.
