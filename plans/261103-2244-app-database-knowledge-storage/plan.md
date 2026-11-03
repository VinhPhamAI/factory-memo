# Knowledge API on the application database

Status: proposal; not implemented. Scope: move the existing knowledge-base, upload,
processing, review and retrieval logic from `DATABASE_URL` storage to the
application tables in `backend/src/storage/orm/` (`APP_DATABASE_URL`). Behavior that
the current code does not implement stays out of scope (see [Out of scope](#out-of-scope)).

Agreed decisions: new data only, no import. Built-in KBs and existing `DATABASE_URL`
data stay where they are, and the API and worker stop referencing them.

## Execution order

1. [Schema additions and repositories](phase-01-schema-and-repositories.md)
2. [Catalog, uploads and sources](phase-02-catalog-and-sources.md)
3. [Jobs, extraction, duplicates and conflicts](phase-03-jobs-and-extraction.md)
4. [Retrieval and embeddings](phase-04-retrieval-and-embeddings.md)
5. [Cutover and verification](phase-05-cutover-and-verification.md)

Phases 2–4 land on one branch and switch together in phase 5: the API and the worker
must read and write the same database. Build each slice test-first. Follow
[engineering standards](../../docs/development/code-standards.md) and
[database migrations](../../docs/development/database-migrations.md).

## Current state

| Concern | Current storage | Current code |
|---|---|---|
| KB list and create | `knowledge_bases` registry table in `DATABASE_URL`; `CREATE DATABASE kb_<name>` per KB | `backend/api/knowledge_bases.py`, `storage/kb_registry.py`, `storage/databases.py` |
| Upload metadata | `data/uploads/<kb>/<id>/metadata.json`, `processing.json`, `converted.txt` | `storage/document_uploads.py` |
| Source text | `raw_conversations` in `kb_<name>`; created at upload extraction, recording or text submission | `storage/knowledge_store.py`, `services/voice_capture.py`, `api/voice_capture.py` |
| Knowledge records | `knowledge_items` (vector, tsvector, context, links, revision) in `kb_<name>` | `KnowledgeStore` |
| Document chunks | `spec.document_table` + BM25 postings + `rag_indexes` in `kb_<name>` | `agents/rag/store.py` (`DocumentStore`) |
| Jobs | `ingestion_jobs` in `DATABASE_URL` (`kb`, `source_kind`, text `source_id`) | `storage/job_queue.py`, `backend/worker.py` |
| Runs, signatures, conflicts | `extraction_runs`, `content_signatures`, `knowledge_conflicts` in `kb_<name>` | `storage/extraction_runs.py`, `content_signatures.py`, `knowledge_conflicts.py` |
| Review actor | Fixed string `knowledge management UI (unauthenticated)` | `services/knowledge_management.py` |
| Retrieval | Agent tools and the Industrial QA pipeline call `search_knowledge` / document search | `agents/agent/tools.py`, `knowledge_bases/industrial_qa/retrieval.py` |

Only `users` and `auth_sessions` use `APP_DATABASE_URL` today. KB, upload,
management and capture routes are unauthenticated.

Services reach storage through injectable factories (`open_store`, `open_runs`,
`open_conflicts`, `open_document_store`, `queue`, `registry_factory`). That narrow
surface is the migration seam:

| Legacy interface | Calls used by services |
|---|---|
| `KnowledgeStore` | `add_conversation`, `get_conversation`, `list_conversations`, `update_conversation_metadata`, `mark_conversation`, `find_processed_conversation`, `add_knowledge`, `embed_knowledge`, `get_knowledge`, `knowledge_for_evidence`, `review_knowledge`, `similar_knowledge`, `lexical_knowledge`, `search_knowledge`, `update_knowledge_scope` |
| `DocumentStore` | `add_chunks`, `get_document`, `document_summaries`, `review_chunk`, `search`/`search_documents`, `adjacent_chunks`, `ensure_config` |
| `JobQueue` | `enqueue`, `claim`, `heartbeat`, `complete`, `fail`, `release`, `recover_stale`, `latest`, `listener` |
| `ExtractionRunStore` | `start`, `finish`, `find_active`, `find_active_original`, `for_evidence`, `overlap_candidates`, `unsigned`, `clear` |
| `ConflictStore` | `put`, `get`, `get_row`, `current`, `for_records`, `list_rows`, `set_review`, `clear` |

## Feature mapping

| Feature | Supported today | Migrated in this plan |
|---|---|---|
| KB CRUD | List and create (templates, `KB_MAX_CREATED`) | List and create on `knowledge_bases`; no per-KB database. Built-in KBs are no longer listed or served by the API. |
| OCR | No (standalone `deployments/ocr` is not called by ingestion) | No; keep the converter boundary |
| Format support | Plain-text conversion only (`text_convertible`) | Same converter; text goes to `knowledge_sources.extracted_text` |
| Embeddings | `EMBEDDING_*` settings; `knowledge_items` dimension check; document index records model and size | Dimension check against `knowledge_chunks.embedding`; model recorded in run settings |
| Ingestion jobs | convert → extract → optional conflict; leases, retries, one active job per source, next stage in one transaction, no duplicate chunks on retry | Same behavior on ORM `ingestion_jobs` |
| Source links | `knowledge_items.evidence_id`; document chunk `doc_id` and index; original download | `knowledge_chunk_sources` with source and passage references |
| Duplicates and conflicts | Exact-content blocking, near-duplicate confirmation, conflict judging and review | Same behavior on ORM `extraction_runs`, `content_signatures`, `knowledge_conflicts` |
| Retrieval API | No standalone endpoint; agent/QA search excludes deprecated records | Agent/QA search on `knowledge_chunks` with deletion and status filters |
| KB UI | Knowledge management page: sources, upload, convert/extract, retry, review, conflicts, original file | Unchanged HTTP contracts; bearer token added |

## Target design

- **One engine.** API and worker open `APP_DATABASE_URL` through
  `storage/app_database.py`. Per-KB engines and caches (`SourcePipeline._stores` and
  similar) disappear; repositories receive `knowledge_base_id`.
- **Repositories, not a clone of `KnowledgeStore`.** New modules under
  `backend/src/storage/repositories/` use domain names (sources, chunks) and the ORM.
  Services keep `KnowledgeRecord` as their record model so prompts, normalizers,
  verification and consolidation stay unchanged; repositories map records to rows.
- **Sources exist from submission.** Upload, recording and text submission create a
  `knowledge_sources` row immediately (`received`). Upload bytes stay on disk; the
  `files` row stores metadata. `processing.json` and `converted.txt` are replaced by
  source `status`, `extracted_text`, `metadata` and job/run rows.
- **One searchable table.** Knowledge records and document passages are rows of
  `knowledge_chunks`, distinguished by `details.chunk_kind` (`knowledge` or
  `document_passage`). Each chunk has at least one `knowledge_chunk_sources` link.
- **Only KBs in `knowledge_bases` are served.** The catalog no longer merges built-in
  specs. `industrial_qa` and `general` stay as code packages: `general` is the policy
  template for created KBs; `industrial_qa` stays reachable through its CLI and
  benchmarks on the legacy stores. Built-in names stay reserved for new KBs.
- **No data import.** Existing registry rows, `kb_<name>` databases, upload metadata
  and jobs stay untouched and invisible to the new API. The new tables start empty.
- **Legacy modules stay for CLI and benchmarks.** Benchmark runners, CLI tools, seed
  restore and the Industrial QA snapshot keep the legacy stores until a later cleanup.
  The API and worker stop reading `DATABASE_URL`.

### Field mapping

How repositories map the service models to the application tables.

| Service field | Target |
|---|---|
| `KnowledgeRecord.id` | `knowledge_chunks.id` (`[K:<id>]` citations) |
| `subject`, `claim` | `title`, `content` |
| `type`, `context`, `valid_from`, `valid_to`, `stated_by`, `conflict_with`, `supported_by`, `contradicted_by`, `revision` | `details` fields of the same names |
| `search_text`, `embedding` | `search_text`, `embedding` |
| `status` (`draft`/`approved`/`deprecated`; reject maps to `deprecated`) | `status`; `reviewed_by`/`reviewed_at` from the signed-in reviewer; record review history in `details.revision.history`, passage review history in `details.review_history` (the shapes the stores keep today) |
| `superseded_by` | `superseded_by` |
| `evidence_id` | `knowledge_chunk_sources(source_id, references=[{"type": "source"}])` |
| Conversation `id`, `transcript`, `stated_by`, `metadata` | `knowledge_sources.id`, `extracted_text`, `metadata` (`stated_by` inside) |
| Conversation status `pending`/`processed`/`failed`, `error` | `received`/`processed`/`failed`; error stays on the job or run |
| Document passage `doc_id`, index, text, review status/history | Chunk UUID; `details.doc_id` (`[D:<id>]` citations), `details.chunk_index`, link `references=[{"type": "passage", "chunk_index": i}]` |
| Upload filename, size, sha256, storage key | `knowledge_sources.id` (= upload ID in responses) and `files` row |

## Decisions

Agreed on 2026-11-03.

| # | Decision | Agreed choice |
|---|---|---|
| D1 | Authentication on KB routes | Bearer token required on all KB, upload, management and capture routes. Create KB and review records/conflicts: `expert` or `admin`. Upload, text, recording and extract: any active user. WebSocket recording sends the access token in its `start` message. `created_by`, `submitted_by`, `uploaded_by` and `reviewed_by` are the signed-in user. |
| D2 | Built-in KBs and existing data | No `system` account. Built-in KBs and existing data stay untouched; the API and worker no longer reference them. |
| D3 | Keyword ranking | `ts_rank_cd` on `search_vector` for both chunk kinds; no postings table. Acceptance is functional, not benchmark parity. |
| D4 | Cutover | No data import. Deploy the new version on empty application tables; legacy databases stay as they are. |
| D5 | Source URLs with `upload`/`conversation`/`document` kinds | Keep the paths; every kind resolves to one source ID. |

## Out of scope

- OCR, PDF/DOCX/XLSX/image parsing and page, paragraph or cell-range evidence.
- KB view, rename and delete endpoints; authorized-file add/remove; one active source
  per identical upload (exact duplicates are still blocked at extraction).
- A standalone retrieval endpoint and chunk search in the UI.
- Moving benchmark runners, run databases and CLI tools off the legacy stores.
- Re-running or comparing Industrial QA benchmark results.
- Importing existing KBs, sources, records, jobs, seed data or snapshots.
- Serving `industrial_qa` (or other built-in KBs) through the API.
- Removing legacy modules and `DATABASE_URL` databases.

## Risks

- **Ranking drift.** Porting search SQL or adopting D3 can change result order.
  Benchmark re-runs are out of scope; functional search tests cover the filters and
  evidence.
- **Data no longer visible.** After deployment, the UI shows no existing KBs,
  sources or records, and the `industrial_qa` QA endpoint stops answering. Announce
  this before deploying.
- **Contract drift.** The UI depends on response fields such as `processing`,
  `counts`, `can_convert` and `conversion_note`. Pin them with characterization
  tests before changing storage.
- **Performance.** Legacy per-KB expression indexes on `context->>key` become
  JSON paths in one shared table. Measure context-filtered search before adding
  indexes.
