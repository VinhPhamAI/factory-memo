# Phase 8 — Retrieval API and chunk search

Depends on phases 1–4 and proposed decision D11. It uses phase 6 evidence references
when they are present. Today retrieval only runs inside the agent tools and the
Industrial QA pipeline; no endpoint returns ranked chunks, and the UI cannot search them.

## Goal

- An endpoint returns ranked chunks with their evidence for one KB.
- It excludes deleted, rejected (`deprecated`) and superseded-and-deprecated chunks,
  and chunks without an active source.
- The knowledge management page can search chunks and open their evidence.
- One ranking implementation: the endpoint calls the phase 4 repository search used by
  the agent tools. No endpoint-only scoring or filters.

## Contract

`POST /v1/knowledge-management/{kb}/search` (D11), bearer token, any active user.

Request:

| Field | Type | Default | Rule |
|---|---|---|---|
| `query` | string | required | 1–2000 characters after trimming |
| `mode` | `hybrid` \| `dense` \| `keyword` | `hybrid` | `dense` and `hybrid` need a matching embedding model |
| `kinds` | list of `knowledge`, `document_passage` | both | |
| `statuses` | list of `draft`, `approved` | both | `deprecated` is rejected with 422 |
| `context` | object | `{}` | Same keys and unscoped-match rule as `build_knowledge_filter` |
| `limit` | int | 10 | 1–50 |

Response:

```json
{
  "query": "E-105 trục Z",
  "mode": "hybrid",
  "embedding_model": "…",
  "results": [
    {
      "rank": 1,
      "chunk_id": "0190…",
      "citation": "[K:0190…]",
      "kind": "knowledge",
      "status": "draft",
      "title": "…",
      "content": "…",
      "scores": {"fused": 0.032, "dense": 0.81, "keyword": 0.4},
      "evidence": [
        {"source_id": "0190…", "source_title": "manual.pdf", "source_kind": "document",
         "references": [{"type": "page", "page": 3, "bbox": [91, 321, 365, 365]}],
         "original_url": "/v1/knowledge-management/<kb>/sources/upload/<id>/original"}
      ]
    }
  ]
}
```

- `citation` is `[K:<chunk id>]` for knowledge and `[D:<doc_id>]` for passages, as in answers.
- `scores` lists the components that produced the rank. A missing component is
  `null`, not `0`.
- Evidence lists only active linked sources.
- Never returned: embeddings, `search_text`, raw transcripts beyond the chunk's own
  content, reviewer notes.
- Errors: 401, 404 (unknown or deleted KB), 422 (validation), 409 when
  `EMBEDDING_MODEL` differs from the KB's runs (phase 4 message) for `dense`/`hybrid`,
  and 503 when the database or embedding provider is unreachable. `keyword` works
  without the embedding provider.

```python
@router.post("/{kb}/search", response_model=SearchResponse)
async def search_chunks(kb: str, request: SearchRequest,
                        user: Account = Depends(require_current_user)) -> SearchResponse:
    """Rank a KB's searchable chunks for a query and attach their active evidence."""
    target = await catalog.require(kb)
    hits = await chunks.search(target.id, request.query, mode=request.mode, kinds=request.kinds,
                               statuses=request.statuses, context=request.context,
                               limit=request.limit)
    return SearchResponse.from_hits(request, hits, await sources.evidence_for(target.id, hits))
```

## Search implementation

- Extend the phase 4 search in `backend/src/storage/repositories/chunks.py` with
  `kinds`, `statuses` and `mode`. Agent tools keep calling it with their current
  arguments, so their results do not change.
- Evidence is loaded in one query for all hits (`knowledge_chunk_sources` joined with
  active sources and files), not per hit.
- Log the KB, mode, result count and latency; do not log the query text.

## UI (`frontend/src/pages/knowledge-management.tsx`)

- A "Tìm kiếm" ("Search") tab: query box, mode selector, kind and status filters.
- Each result shows rank, kind, title, content excerpt and a status badge. Drafts are
  visibly distinct from approved records (product rule).
- Evidence chips open the phase 6 evidence viewer; before phase 6, they open the
  source detail and passage.
- Empty results show "Không tìm thấy" ("Nothing found"). The 409 model mismatch
  shows the server message.

## Affected files

- New: `backend/api/knowledge_search.py` (or a route in `knowledge_management.py`),
  with request/response models.
- `backend/src/storage/repositories/{chunks,sources}.py`, `backend/app.py` (router).
- New: `frontend/src/services/knowledge-search.ts`; the page and its tests.
- Docs: `docs/features/knowledge-management.md` (search section and contract).

## First failing tests

- Ranking: with a stub embedder and fixed vectors, hybrid order matches the agent
  tool's order for the same query.
- Exclusion: deprecated, soft-deleted, other-KB and removed-source chunks never
  appear, in each mode.
- `statuses=["approved"]` returns only approved chunks; `statuses=["deprecated"]`
  returns 422.
- `keyword` works with the embedding provider unreachable; `hybrid` returns 503.
- A model mismatch returns 409 with both model names.
- No token gives 401; an unknown KB gives 404.
- The response has no `embedding` or `search_text` fields.
- UI: a query renders results with status badges, and an evidence chip opens the viewer.

## Completion checks

- API and repository tests pass; agent tool tests unchanged.
- In a running stack: search a KB with records and passages, filter by status,
  open each result's evidence.
- Frontend build, lint and touched tests pass.
