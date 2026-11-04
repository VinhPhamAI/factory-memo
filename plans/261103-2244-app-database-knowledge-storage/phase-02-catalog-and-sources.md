# Phase 2 — Catalog, uploads and sources

Depends on phase 1 and decisions D1, D2 and D5.
Starts from empty application tables; nothing is read from `DATABASE_URL`.

## Contract

Existing paths and response fields stay. Changes:

| Route | Change |
|---|---|
| `GET /v1/knowledge-bases` | Bearer required. Lists only rows of `knowledge_bases`; built-in KBs are no longer listed (`builtin` is always `false`). |
| `POST /v1/knowledge-bases` | Bearer with `expert` or `admin`; otherwise 403. Inserts one row with `created_by` = current user; no database is created. 409 for a used name, a soft-deleted name or a reserved built-in name (`industrial_qa`, `general`). 403 at `KB_MAX_CREATED`. |
| `POST /v1/documents/{kb}/uploads` | Bearer required. Response `id` is the source ID. |
| `POST /v1/voice/{kb}/texts`, `/recordings`, WebSocket `/record` | Bearer required (WebSocket: token in `start`). Creates a `text` source. |
| `/v1/knowledge-management/{kb}/sources...`, `/v1/knowledge-agent/{kb}/stream` | Bearer required. Every kind resolves to a source ID. An unknown KB, including a built-in name, returns 404. |

401 for a missing or unusable token, 503 when the application database is
unreachable. Pin both in tests.

## Changes

- **`backend/api/knowledge_bases.py`.** `KnowledgeBaseCatalog` reads and writes
  through `KnowledgeBaseRepository`. Remove `DatabaseAdmin`, the per-KB database
  create/drop rollback and the merge with built-in specs. Each row becomes
  `custom_spec(name, template=row.template)`. Keep the listener mechanism so new KBs
  are served without restart. Cache name → ID.
- **`backend/src/storage/document_uploads.py`.** Keep byte storage (`save` streaming,
  size limit, `object_dir`, original download). Metadata, processing state and
  converted text move to the source repository. New uploads use the storage key
  `<kb>/<source_id hex>/content`. Existing upload directories are not read.
- **`backend/api/document_uploads.py`.** Save bytes, then create `files` +
  `knowledge_sources(kind="document", status="received")` in one transaction with
  `uploaded_by`/`submitted_by` = current user. Remove the stored object if the
  transaction fails. `extract_mode` queueing keeps its current failure reporting.
- **`backend/src/services/voice_capture.py`, `backend/api/voice_capture.py`.** Save
  transcripts as `kind="text"` sources. The audio file gets a `files` row referenced by
  `file_id`; speakers, cleaning and recording details stay in `metadata`. Cleaning
  writes a new metadata entry and never replaces the original transcript.
- **`backend/src/services/knowledge_management.py`.** `inventory` reads one page of
  sources with chunk counts per status and the latest job per source, instead of
  merging upload directories, `raw_conversations` and document summaries. `detail`
  returns `text` from `extracted_text`, records and passages from chunks of the
  source, and `conversation` from metadata. Derive the UI `kind`
  (`upload` when a document source has a file, otherwise `conversation`) and
  `source_kind` values the UI shows. Replace `REVIEW_ACTOR` with the current user.
- **`backend/app.py`.** One application engine for auth and knowledge services.
  Startup fails with a clear message when `APP_DATABASE_URL` is unset outside tests.
- **Frontend (`frontend/src/services/knowledge-management.ts`, `document-api.ts`,
  `knowledge-base-admin.ts`, `voice-api.ts`).** Send the bearer token from the
  shared in-memory session client. Keep `AuthClient.authorizedFetch()` as a
  single-attempt request: it never refreshes or retries, and returns a 401 response
  unchanged to its caller. Callers keep their existing error handling. WebSocket
  capture sends the current access token in its `start` message. Protected file and
  audio downloads use authenticated fetches because `<a>` and `<audio src>` cannot
  attach an `Authorization` header.

Illustrative upload flow:

```python
async def store_upload(kb: KnowledgeBaseRef, upload: UploadFile, user: Account) -> Source:
    """Save the original bytes, then record the file and its source together."""
    stored = await run_in_threadpool(files.save, kb.name, upload.file, filename=upload.filename)
    try:
        return await sources.create_upload(kb.id, stored, submitted_by=user.id)
    except SourceStorageError:
        await run_in_threadpool(files.remove, stored.storage_key)
        raise
```

## First failing tests

- `test_knowledge_base_catalog.py`: create inserts a row with the template and the
  creator; a built-in name returns 409; listing after restart includes created KBs;
  built-in KBs are not listed; an `employee` gets 403 on create.
- `test_document_uploads.py`: an upload creates one file and one received source;
  a failed source insert leaves no object on disk; no token returns 401.
- `test_knowledge_management.py`: inventory and detail return the pinned fields for
  an upload source extracted in knowledge mode, one extracted in documents mode, and
  a text source.
- `test_voice_capture.py`: a typed text creates a `text` source with `submitted_by`.
- Frontend: requests carry `Authorization`; `authorizedFetch()` returns a 401
  without refreshing or retrying; WebSocket capture includes the access token in
  `start`; protected file and audio requests use authenticated fetches.

## Completion checks

- Characterization tests written before the change pass after it with only the
  agreed contract differences.
- `npm run build`, `npm run lint` and the touched frontend tests pass.
