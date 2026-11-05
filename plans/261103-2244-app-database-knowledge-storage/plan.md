# Knowledge API on the application database

Status: phases 1–4 implemented; phases 6–8 not implemented. The former phase 5
(cutover and verification) was dropped; phase numbers are kept. Scope:

- Phases 1–4 move the existing knowledge-base, upload, processing, review and
  retrieval logic from `DATABASE_URL` storage to the application tables in
  `backend/src/storage/orm/` (`APP_DATABASE_URL`).
- Phases 6–8 add features the current code lacks: formats and OCR, KB and source
  management, and a retrieval API. They run after phase 4.

Remaining exclusions are listed in [Out of scope](#out-of-scope).

Agreed decisions: new data only, no import. Built-in KBs and existing `DATABASE_URL`
data stay where they are, and the API and worker stop referencing them.

## Execution order

1. [Schema additions and repositories](phase-01-schema-and-repositories.md)
2. [Catalog, uploads and sources](phase-02-catalog-and-sources.md)
3. [Jobs, extraction, duplicates and conflicts](phase-03-jobs-and-extraction.md)
4. [Retrieval and embeddings](phase-04-retrieval-and-embeddings.md)
6. [Formats, OCR and located evidence](phase-06-formats-ocr-and-evidence.md)
7. [Knowledge base and source management](phase-07-knowledge-base-and-source-management.md)
8. [Retrieval API and chunk search](phase-08-retrieval-api-and-chunk-search.md)

Phases 2–4 land on one branch and switch together: the API and the worker
must read and write the same database. Phases 6–8 start after phase 4. Each lands
on its own branch, in any order: phase 8 shows phase 6 locations when they exist and
falls back to whole-source evidence otherwise. Build each slice test-first. Follow
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

| Feature | Supported today | Phases 1–4 (migration) | Phases 6–8 (new behavior) |
|---|---|---|---|
| KB CRUD | List and create (templates, `KB_MAX_CREATED`) | List and create on `knowledge_bases`; no per-KB database. Built-in KBs are no longer listed or served by the API. | Phase 7: view, rename (title), soft delete; add/remove source files; one active source per identical file |
| OCR | No (standalone `deployments/ocr` is not called by ingestion) | No; keep the converter boundary | Phase 6: OCR through a hosted `ai-services-api` at `OCR_API_URL` for images and scanned PDFs; provider failures reach the job |
| Format support | Plain-text conversion only (`text_convertible`) | Same converter; text goes to `knowledge_sources.extracted_text` | Phase 6: PDF, DOCX, XLSX converters; one fixture per format |
| Embeddings | `EMBEDDING_*` settings; `knowledge_items` dimension check; document index records model and size | Dimension check against `knowledge_chunks.embedding`; model recorded in run settings | — |
| Ingestion jobs | convert → extract → optional conflict; leases, retries, one active job per source, next stage in one transaction, no duplicate chunks on retry | Same behavior on ORM `ingestion_jobs` | — |
| Source links | `knowledge_items.evidence_id`; document chunk `doc_id` and index; original download | `knowledge_chunk_sources` with source and passage references | Phase 6: page, region, paragraph, cell-range and image references |
| Duplicates and conflicts | Exact-content blocking, near-duplicate confirmation, conflict judging and review | Same behavior on ORM `extraction_runs`, `content_signatures`, `knowledge_conflicts` | Phase 7: identical upload returns the active source |
| Retrieval API | No standalone endpoint; agent/QA search excludes deprecated records | Agent/QA search on `knowledge_chunks` with deletion and status filters | Phase 8: `POST /v1/knowledge-management/{kb}/search` with ranked chunks and evidence |
| KB UI | Knowledge management page: sources, upload, convert/extract, retry, review, conflicts, original file | Unchanged HTTP contracts; bearer token added | Phases 6–8: open located evidence, manage KB and sources, search chunks |

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
| D4 | Cutover | No data import. Deploy the new version on empty application tables; legacy databases stay as they are. No separate cutover phase: run `alembic upgrade head` before starting the new code. |
| D5 | Source URLs with `upload`/`conversation`/`document` kinds | Keep the paths; every kind resolves to one source ID. |

### Proposed decisions for phases 6–8

Not agreed yet; confirm before starting the phase.

| # | Phase | Decision | Proposed choice | Alternative |
|---|---|---|---|---|
| D6 | 6 | Where located segments are stored | `metadata.conversion.segments` (offsets + location), no schema change | A `source_segments` table, if metadata size or querying becomes a problem |
| D7 | 6 | Parser dependencies | Add `python-docx` and `openpyxl`; PDF text via PyMuPDF (already locked) | Route DOCX/XLSX through a document-conversion service |
| D8 | 7 | Meaning of rename | Change `title` and `description`; `name` stays stable (it is in URLs, thread IDs and storage keys) | Rename `name` with redirects |
| D9 | 7 | Who deletes KBs and removes sources | KB delete: `admin`. Source removal: `expert`, `admin` or the submitting user | `expert` and `admin` for both |
| D10 | 7 | One active source per identical file | `knowledge_sources.original_sha256` with a unique partial index; a duplicate upload returns the existing source (200, `duplicate: true`) | Advisory lock and lookup without a schema change |
| D11 | 8 | Retrieval route and access | `POST /v1/knowledge-management/{kb}/search`, any active user | A route under `/v1/knowledge-bases/{kb}/search` |

## Out of scope

- Per-user or per-KB access lists (the schema leaves them for later); every active
  user reads every active KB.
- Office OCR (scanned images inside DOCX/XLSX), storing OCR figure crops, transcription
  of uploaded media, and KB restore or hard delete.
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
- **OCR availability.** Phase 6 needs a hosted `ai-services-api` with the GPU OCR
  worker, reached at `OCR_API_URL`. Original files leave the host, so a remote URL needs
  HTTPS and an authenticating proxy. When it is unreachable, scanned files retry and
  then fail with a clear message. OCR accuracy on
  factory documents is unevaluated.
