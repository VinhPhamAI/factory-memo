# Phase 3 — Jobs, extraction, duplicates and conflicts

Depends on phases 1 and 2. Preserves the rules in `source_pipeline.py` and
`job_queue.py` exactly; only storage changes.

## Ingestion jobs

- **`backend/src/storage/repositories/ingestion_jobs.py`** replaces `JobQueue` for the
  API and worker. Keep: `pg_notify` on enqueue in the same transaction, `LISTEN` plus
  polling, `FOR UPDATE SKIP LOCKED`, heartbeats, stale recovery, `max_attempts`
  with backoff, `options` carried to every stage, and next-stage creation inside the
  completing transaction.
- Jobs reference `(knowledge_base_id, source_id)`. Drop `source_kind`; the source
  row has the kind. `latest()` returns jobs keyed by source ID.
- Update source `status` in the same transaction as the job transition: `processing`
  when claimed, `processed` when the last stage completes, `failed` when the job fails
  without retry. A requeued job leaves the source `processing`.
- **`backend/worker.py`, `backend/src/services/job_worker.py`.** Open
  `APP_DATABASE_URL`; stop importing `server_url_from_env`.

## Stage handlers (`backend/src/services/source_pipeline.py`)

- **convert.** Read the file bytes, run the existing plain-text converter and write
  `extracted_text` plus `metadata.conversion` (`encoding`, `sha256`, `at`). An
  unsupported type fails without retry with the existing `NO_CONVERTER` message.
  Keep the converter behind one function so OCR and format parsers can be added later.
- **extract, knowledge mode.** Run the KB ingestion service unchanged. Write records,
  embeddings and `knowledge_chunk_sources` links in one transaction. Consolidation
  link updates (`supported_by`, `contradicted_by`, `superseded_by`) update `details`
  and `superseded_by` without overwriting history.
- **extract, documents mode.** Chunk with `chunk_document` and the KB chunking
  settings. Store passages as `chunk_kind = "document_passage"` with
  `details.doc_id`, `details.chunk_index` and a passage reference.
- **Retry safety.** `has_results` checks for chunks linked to the source in the job's
  mode. A retried job with results completes without running again.
- **conflict.** Unchanged detector; verdicts go to `knowledge_conflicts` with
  `knowledge_base_id` and `job_id`; the summary goes to source `metadata.conflicts`.
- Remove `backfill`/`record_legacy` from the serving path; there is no legacy data
  to backfill.

## Duplicates

- **Exact.** Start an `extraction_runs` row before extracting. An active run with the
  same `(knowledge_base_id, content_sha256, mode, fingerprint)` completes the job as
  `duplicate` with a link to the earlier source, without an LLM call. Keep the
  original-content check (`find_active_original`) and its messages.
- **Near.** Save signatures after conversion. Matches above the thresholds complete
  the job as `near_duplicate` until a job with `confirm_overlap` runs. Scores only warn.

## Review

- `review_record` and `review_chunk` set `status`, `reviewed_by`, `reviewed_at` and
  append to the review history (`details.revision.history` for records,
  `details.review_history` for passages). Reject maps to `deprecated`. Reset to draft
  clears reviewer fields, as the database rule requires them together.
- Conflict confirm and dismiss append a review entry and never change chunk status.
  Supersede deprecates the unselected chunk and sets its `superseded_by`.
- Recheck queues conflict jobs for edited chunks, as today.

## First failing tests

- `test_knowledge_management.py` / `test_source_pipeline` cases against the
  repositories: convert writes `extracted_text`; extract writes chunks with links;
  a retried extract job adds no chunks; an identical second upload completes as
  `duplicate`; a near duplicate waits for confirmation and extracts after it.
- A failing provider call marks the job for retry and keeps the source `processing`;
  the final failure sets the source `failed` and the job `error` shown in inventory.
- Review sets `reviewed_by` to the current user; conflict confirm leaves both chunks'
  status unchanged; supersede deprecates only the unselected chunk.

## Completion checks

- `uv run python -m unittest backend.tests.test_knowledge_management backend.tests.test_job_queue -v`
  and the new repository integration tests pass.
- A local upload → convert → extract → conflict run completes with a worker on
  `APP_DATABASE_URL` only.
