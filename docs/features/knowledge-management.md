# Knowledge management

Administrators manage knowledge bases at `/admin/knowledge-bases`. The page
lists active KBs, creates and edits them, selects the Library default, and
soft-deletes non-default KBs after confirmation. Existing `/knowledge` URLs
redirect to **Quản lý tài liệu** (`/files`); source details remain available via
source links.

Experts and administrators can create and edit KBs through the API. Only
administrators can access the administration page, choose the default or delete
KBs. At most one active KB is the default. If none is selected, Library uploads
are disabled until an administrator selects one. Changing the default affects
future uploads; it does not move existing sources or files.

Removing a source retains its bytes, linked chunks and conflicts for history. A
chunk remains searchable while it has another active evidence source. The original
source download returns `404` after removal.

## Manage a knowledge base

1. Open **Quản trị knowledge base** with an administrator account.
2. Create a KB or select one to inspect its counts, edit its title and description,
   or delete it. The stable code cannot be changed.
3. Choose **Đặt làm mặc định** to direct new Library uploads to that KB. Select
   another default before deleting the current default.

| API | Access | Response |
|---|---|---|
| `GET /v1/knowledge-bases` | Active user | `200` catalog with `is_default` for each active KB. |
| `PUT /v1/knowledge-bases/{kb}/default` | Admin | `204` selects an active KB; `404` if missing. |
| `GET /v1/knowledge-bases/{kb}` | Active user | `200` with `name`, `title`, `description`, `template`, `created_by` (username), `created_at`, `updated_at`, `source_counts` by active source status and `chunk_counts` by stored chunk kind and status |
| `PATCH /v1/knowledge-bases/{kb}` | Expert or admin | `200` with the updated detail. Send a nonempty `title` (up to 80 characters), `description` (up to 500, empty clears it), or both. Sending `name` returns `422`. |
| `DELETE /v1/knowledge-bases/{kb}` | Admin | `204`. An unknown or already deleted KB returns `404`; deleting the default returns `409`. |
| `DELETE /v1/knowledge-management/{kb}/sources/{kind}/{source_id}` | Submitter, expert or admin | `204`, `409` while an ingestion job is running, or `404` for an unknown or removed source. |
| `GET /v1/knowledge-management/{kb}/sources?include_removed=true` | Active user | Includes removed sources with their `removed_at` timestamps. The default inventory hides them. |

The routes return `401` for unusable bearer tokens, `403` for insufficient
permissions and `503` when the application database is unavailable.

## Source lifecycle

### Conflict review

Conflict detection and review remain available through the API. The management
UI does not provide a conflict review page.

The application runs conflict detection for knowledge extraction in KBs created
from the General template, including existing KBs. Conflict jobs, pairs and
review history are stored in `APP_DATABASE_URL`. Review requires an expert or
administrator account.

A review can confirm or dismiss a suspected conflict, or explicitly supersede
one record. These decisions do not approve either record. Supersession retires
the unselected record from search while retaining its history. Missing records
and resolved pairs cannot be reviewed; stale judgments must be compared again.
Recheck requests queue background jobs and require a conflict worker.
To check existing application records that have no verdict yet, send
`{"record_ids":["<knowledge-chunk UUID>"]}` to the recheck endpoint. It queues
the source of each supplied record. A request body of `{}` checks for stale pairs.
Legacy verdicts and review history in per-KB databases require a separate data
import with verified chunk mappings.

The source list and detail show conflict job progress and failures separately
from extraction. Source-level counts describe the last detection run, not current
review totals. The API exposes conflict listing, review and recheck endpoints.
Backend integration tests exercise these endpoints and the worker against
disposable PostgreSQL with controlled extraction and judge responses.

### Processing and review

Processing and human review are independent:

| Stage | Meaning |
| --- | --- |
| `raw` | The original upload is stored. This does not imply that it has been read, OCRed or indexed. |
| `converted` | Text is available from a transcript or a supported text, PDF, DOCX, XLSX, PNG or JPEG upload. |
| `extracted` | Knowledge items or document chunks have been generated. Results still need human review. A completed run can also contain zero new items, e.g. consolidation only added support. |

TXT uploads can go **directly from raw to extraction**. In the source list, check
one or more sources (or **select all** visible ones) and use **Convert** or
**Extract** in the bar above the list; Extract on a raw source queues conversion
followed by extraction. Each button applies only to the checked sources that
support that step and shows how many; running sources and sources without a
converter cannot be checked. Sources whose request fails stay checked for a retry.
The worker detects file content before selecting a converter:

- text preserves strict UTF-8/UTF-16 decoding and original line endings;
- PDF text extraction preserves page and block bounding boxes;
- DOCX preserves paragraph and table-cell positions;
- XLSX renders sheet headings and tab-separated rows with cell ranges;
- PNG, JPEG and PDFs below `PDF_MIN_TEXT_CHARS_PER_PAGE` average text characters
  per page use the hosted OCR gateway.

Converted text is saved in `knowledge_sources.extracted_text`. Conversion metadata
records the converter, version, digest, timestamp, provider details and exact
located segments. Invalid or empty files are refused; original bytes remain intact.
Office image OCR, PPTX and uploaded audio/video conversion are not implemented.
Microphone recordings already have ASR transcripts and start in converted.

The inventory lists `knowledge_sources` from `APP_DATABASE_URL`. Knowledge records
and document passages appear through `knowledge_chunk_sources`, so one source is
shown once with counts by review status. Existing legacy `raw_conversations`,
per-KB chunk tables and upload sidecars are not merged into this view. Source lists
have a **Tải thêm** button; filters and counts operate on loaded entries. Original
uploads can be downloaded as authenticated attachments.

## Extraction modes

Choose a mode **per source**, before extraction:

- **Knowledge items** uses that KB's extraction prompt, schema, normalizers,
  verification and consolidation factory, including the shared Industrial QA
  ingestion policy. The original text is saved as evidence first. The 100,000
  character limit applies to this interactive extraction mode.
- **Document chunks** uses the shared RAG chunker and the KB's chunking/index
  configuration, then embeds and persists chunks. No LLM extraction is involved.
  A configuration with no word limit produces one full-document chunk.

Convert and Extract only queue jobs (HTTP 202 with the job); the workers run
them in the background, and the page shows the queued/running stage, scheduled
retries and failures. Retrying reuses the evidence ID. A source
with existing results cannot be extracted again or switched to another mode;
its results remain available for review. This avoids silently replacing reviewed
claims. New microphone captures made by the UI send `extract: false` and wait for
mode selection here. Existing WebSocket and WAV API clients still default to
automatic knowledge extraction unless they also send `extract=false`.

Selecting a source mode **does not change the KB's QA retrieval policy**. For
example, storing document chunks in a knowledge-only KB does not automatically
make that KB's ReAct agent search the document index. The UI explains this before
extraction. Model/embedding calls use configured providers and can incur costs.

## Human review

Both knowledge items and chunks start in `draft` (Chờ duyệt). **Duyệt** sets
`approved`, **Loại bỏ** sets `deprecated`, and **Hoàn tác** returns to `draft`.
Decisions append time, note and the signed-in reviewer's account ID to history. Review does
not rewrite source text or remove historical results. Superseded knowledge
records cannot be reapproved. Drafts remain searchable in the applicable index;
deprecated records/chunks are excluded from search and retained for citations.
Legacy chunks without explicit review metadata are shown as unreviewed drafts;
this is not an assertion that they were approved previously.

Recording sources retain the speaker names, original/cleaned transcript views,
quote navigation and audio playback alongside the shared review controls.
Knowledge approval checks remain human decisions: source-faithfulness checks do
not establish engineering correctness or make synthetic content an approved SOP.

For converted uploads, each knowledge quote or document passage links to every
exact segment it overlaps: PDF/OCR page and optional region, DOCX paragraph,
XLSX sheet and cell range, or image area. An exact knowledge quote that cannot be
found falls back to the whole source with `location_missing: true`; locations are
never interpolated. The review UI opens PDF pages, marks image areas and labels
paragraphs or cell ranges. OCR accuracy on Vietnamese factory documents has not
been evaluated, so these locations and extracted text still require review.

## Storage and APIs

Uploads keep original `content` bytes in the object directory and Docker volume.
File metadata, converted text, processing state and source metadata live in the
application database. Evidence and knowledge records use `knowledge_sources`,
`knowledge_chunks` and their source links. The
`document_upload` evidence kind distinguishes an uploaded document from a
conversation; it has no invented speakers or dialogue turns. Chunk metadata
retains evidence IDs, original upload IDs, transcript hashes and review history.
All management endpoints require a bearer access token. Record, passage and
conflict review additionally require an expert or administrator account.

All endpoints below are under `/v1/knowledge-management/{kb}`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/sources?offset=0` | Source inventory and partial-availability warnings |
| GET | `/sources/{kind}/{id}` | Source, text preview, processing and review results |
| GET | `/sources/upload/{id}/original` | Download original bytes |
| GET | `/sources/upload/{id}/pages/{page}` | Render one PDF page as PNG for a region highlight |
| POST | `/sources/upload/{id}/convert` | Queue conversion of a supported file (202 `{job}`) |
| POST | `/sources/{kind}/{id}/extract` | `{"mode":"knowledge"}` or `{"mode":"documents"}`; queues convert → extract for a raw upload (202 `{status, mode, job}`) |
| POST | `/knowledge/{record_id}/review` | Knowledge decision and optional note |
| POST | `/sources/{kind}/{id}/review-chunk` | Source-scoped chunk decision, chunk ID and optional note |
| POST | `/search` | Ranked chunk search with evidence; see [Search](#search) |

Kinds are `upload`, `conversation` (existing evidence-table IDs) and `document`
(URL-safe token for an indexed document ID). The legacy voice review endpoints
remain available for existing clients. Text previews are limited to 100,000
characters; document chunk processing still reads the whole converted text.

A queued or running job returns 409 for a second request on the same source.
Source details include the latest job in `processing.job` (stage, status, mode,
next stages, attempts, error and retry time). Extract also returns 409 with
`detail.code = "duplicate_content"` when identical content was already extracted,
or `detail.code = "near_duplicate"` (with `matches`) when the content nearly
matches an extracted source and the request has no `confirm_overlap: true`
(see below).

## Search

`POST /v1/knowledge-management/{kb}/search` ranks the searchable chunks of one
knowledge base and returns each with its active evidence. Any signed-in user can
call it. The management UI does not provide a search page.

Hybrid search combines vector and full-text ranks using reciprocal rank fusion.
When the requested kinds include `knowledge`, confident hits rank first using
the same similarity and term overlap rules as the agent's knowledge search.

Request:

| Field | Default | Rule |
| --- | --- | --- |
| `query` | required | 1–2000 characters after trimming |
| `mode` | `hybrid` | `hybrid`, `dense` (vector only) or `keyword` (full text only) |
| `kinds` | both | `knowledge`, `document_passage` |
| `statuses` | both | `draft`, `approved`; `deprecated` returns 422 |
| `context` | `{}` | Context keys and values; chunks without a key still match it, as for the agent |
| `limit` | 10 | 1–50 |

Response: `query`, `mode`, `embedding_model` (`null` for `keyword`) and `results`.
Each result has `rank`, `chunk_id`, `citation` (`[K:<chunk id>]` or `[D:<doc id>]`),
`kind`, `status`, `title`, `content`, `scores` and `evidence`.

- `scores.fused`, `scores.dense` and `scores.keyword` name the rankings that produced
  the chunk; a component that did not is `null`, not `0`. `fused` exists only in
  `hybrid` mode.
- `evidence` lists only active sources: `source_id`, `source_title`, `source_kind`,
  `references` (page, paragraph, cell range or source location) and `original_url`
  (`null` when the source has no stored file).
- Never returned: embeddings, `search_text`, raw transcripts beyond the chunk's own
  content, reviewer notes.
- Drafts are returned with `status: "draft"`; the UI marks them as unreviewed.
  Search rechecks chunk status and source visibility when it loads the final
  results and evidence together. Deleted, deprecated (including rejected) and
  superseded chunks and chunks without an active source are excluded.

Errors:

| Status | Cause |
| --- | --- |
| 401 | Missing or invalid token |
| 404 | Unknown or deleted knowledge base |
| 409 | `dense` or `hybrid` with an `EMBEDDING_MODEL` that differs from the model that embedded the knowledge base; the message names both models |
| 422 | Invalid field, including a `deprecated` status |
| 503 | Database unreachable, or the embedding provider is missing or unreachable (`dense`, `hybrid`) |

`keyword` needs no embedding provider and no model check, so it still works when the
provider is down or the model changed. Each search logs the knowledge base, mode,
result count and latency; the query text is not logged.

In the UI, an evidence chip opens the source page and, when the reference has a
location, the located evidence preview.

## Duplicate content

An active source with the **same** content and mode blocks a repeat extraction.
Content that is **nearly** the same as an active extracted source needs the
reviewer's confirmation. See [near-duplicate content](#near-duplicate-content).
Once the source is removed, another source can extract that content. The original
file, extraction history and evidence links are retained.

- **Content fingerprint:** SHA-256 of the evidence text after Unicode NFC and
  whitespace collapse, so re-uploads that differ only in line endings or spacing
  match. Case, punctuation and wording changes are different content. Before
  conversion a raw upload is compared by the SHA-256 of its original bytes.
- **Extraction fingerprint:** SHA-256 of the mode and its settings: extraction
  provenance (pipeline version, prompt, model and schema/ontology hashes) for
  knowledge items, chunking/index settings for document chunks.
- **`extraction_runs`** in `APP_DATABASE_URL` records each extraction of an
  evidence row: content and extraction fingerprints, original-byte hash, job ID,
  status (`running`, `done`, `failed`, `duplicate`) and result counts. A unique
  partial index allows one `running`/`done` run per KB, content, mode and
  fingerprint among active sources. A transaction lock serializes starts across
  different fingerprints.

Policy:

| Earlier run of the same content and mode | Effect |
| --- | --- |
| Source removed | Not blocking. The run stays in history |
| `done`, same logic | Blocked: the existing results are reused, not extracted again |
| `done`, older logic | Blocked, reported as extracted with older logic (re-extraction not implemented) |
| `running`, started (or taken over) within `EXTRACTION_RUN_STALE_S` (default 3600 s) | Blocked as "being extracted" |
| `running` for longer | Not blocking; the next start marks it `failed` |
| `failed` | Not blocking |
| Other mode | Not blocking (knowledge items and document chunks are separate results) |

The API checks before queueing and answers 409 with `duplicate_of` (earlier
source name, kind, ID, status, mode, `same_logic` and time). The extract worker
checks again when the job starts, since two copies can be queued before either
runs: the second job completes without an LLM call and the source shows
`processing.status = "duplicate"` with `duplicate_of`. Its evidence stays stored
and can be extracted later if the earlier run fails or its source is removed.
A requeued job of the same source takes over its own running run. Runs have no heartbeat, so set
`EXTRACTION_RUN_STALE_S` above the longest expected extraction.

Extractions made before runs existed are backfilled on the next check: processed
evidence rows (or rows with knowledge records) get a `done` run, or `duplicate`
when an earlier row already holds that content; the fingerprint is taken from
stored extraction provenance, else `legacy`. Runs are history and are never
deleted; no knowledge record or chunk is overwritten or removed.

The UI shows **Trùng nội dung** in the list, a note linking the earlier source
on the detail page, and duplicates separately in bulk results (they are
unchecked because they would be refused again). Legacy voice API clients that
extract in the API process are not checked when they start; their results are
recorded by the backfill afterwards.

## Near-duplicate content

Re-recordings, edited copies and excerpts of a source are different content,
so the exact fingerprint does not catch them. Before an extraction starts, its
text is compared with the sources already extracted (`done` or live `running`
run) **in the same KB and mode**:

- **Shingles:** 5-word runs of the text after Unicode NFC, case folding and
  removal of punctuation and `[Tnn] Speaker X:` turn labels.
- **Candidates:** each extracted text keeps a MinHash signature (128
  permutations) cut into 64 LSH bands of 2 rows in `content_signatures` (KB
  database, GIN index on the bands). Sources sharing a band are candidates;
  the 20 sharing the most bands are scored exactly from their texts.
- **Scores:** Jaccard (shared / all shingles of both), containment (share of
  the new text found in the earlier source) and coverage (share of the earlier
  source found in the new text).

| Level | Rule (default) | Effect |
| --- | --- | --- |
| `near_duplicate` | Jaccard ≥ `NEAR_DUPLICATE_JACCARD` (0.9) | Waits for confirmation |
| `contained` | containment ≥ `NEAR_DUPLICATE_CONTAINMENT` (0.9) | Waits for confirmation |
| `similar` | any score ≥ `NEAR_DUPLICATE_INFO` (0.6) | Information only |

Thresholds are in (0, 1]; an out-of-range value stops the API/worker at start.
Similarity is a warning, never a decision: nothing is merged, overwritten or
discarded because of it, and embedding similarity is not used. Exact duplicates
keep the policy above.

The API answers 409 `near_duplicate` with `matches` (source name, kind, ID,
status, mode, level and the three scores, at most 5, strongest first) and a
message. Extract with `confirm_overlap: true` queues the job; the option is kept
by the job (column `options`) through convert → extract. The extract worker
checks again, because a raw upload has no text before conversion and another
source can finish first. Without confirmation it completes without an LLM call
and leaves `processing.status = "near_duplicate"` with `overlaps`; with
confirmation it extracts and records `overlaps` and `overlap_confirmed`. Auto
mode on upload queues the job anyway, so the worker decides. The confirmed
result is a separate draft of the new source; consolidation still runs as for
any extraction.

The UI shows **Gần giống · chờ xác nhận** in the list, the matches with links
and scores on the detail page with **Vẫn trích xuất** (confirmation dialog), and
near-duplicates separately in bulk results (unchecked; confirm per source).
Overlaps of a finished extraction are shown for information.

Limitations: LSH finds a candidate reliably when Jaccard is at least ~0.2, so an
excerpt of a source more than ~5x longer may be missed even when its
containment is high. Rows extracted before signatures existed are all signed on
the next check (in batches of 100), which makes the first check on a large KB
slower. Signatures are not refreshed when an evidence text
changes after extraction. Legacy voice API clients that extract in the API
process are not checked.

### Tuning thresholds on real data

`backend/scripts/near_duplicate_report.py` is read-only: it loads the extracted
texts of one KB, scores every pair of the same mode exactly and prints the score
distribution and the top pairs with the level each would get at the current
(or given) thresholds. It writes nothing.

```sh
uv run python -m backend.scripts.near_duplicate_report --kb general
uv run python -m backend.scripts.near_duplicate_report --kb general --mode knowledge --top 30 --jaccard 0.85
```

Review the top pairs by hand: lower a threshold when real re-recordings fall
below it; raise it when distinct cases (for example the same checklist on
different machines) are flagged. Set the chosen values in `NEAR_DUPLICATE_*`.
All pairs grow quadratically; use `--limit` on large KBs.

## Job queue and workers

Each stage is a job row in `ingestion_jobs` in `APP_DATABASE_URL` (shared by all
served KBs). Jobs reference the knowledge-base and source IDs; the source row owns
its kind. The pattern is notify-consume:

```text
upload (Auto) / Extract on raw ─► convert job ─ NOTIFY ─► convert worker
                                       │ done: next job in the same transaction
                                       ▼
          Extract on converted ─► extract job ─ NOTIFY ─► extract worker ─► draft items/chunks
                                       │ knowledge mode, KB with conflict detection
                                       ▼
                                  conflict job ─ NOTIFY ─► conflict worker ─► knowledge_conflicts
```

Knowledge-mode extraction marks the source `processed` and queues the conflict
stage in the same transaction. The stage compares the new items with similar
stored items and records suspected conflicts for review. Its status and summary
come from the conflict job. Each verdict or reviewer decision commits with the
corresponding link changes. See [conflict detection](conflict-detection.md) for
the judge and its settings.

- Enqueueing inserts the job and calls `pg_notify` on the stage's channel in one
  transaction. Workers `LISTEN`, claim with `FOR UPDATE SKIP LOCKED` and also
  poll (`WORKER_POLL_S`, default 15 s), so a missed notification only delays a job.
- A job lists the stages that follow it. Completing it creates the next job in
  the same transaction, so a pipeline neither stops between stages nor runs a
  stage twice. Duplicate and unconfirmed near-duplicate results end the pipeline;
  a failed convert queues no extraction.
- At most one queued/running job per source (unique partial index).
- Running jobs heartbeat (`WORKER_HEARTBEAT_S`). A job without a heartbeat for
  `WORKER_STALE_S` (a crashed worker or killed container) is requeued.
  Transient errors retry with exponential backoff (`JOB_RETRY_DELAY_S`, up to
  `JOB_MAX_ATTEMPTS`); a missing converter or unreadable text fails at once.
  A stage whose results already exist completes without running again.
- On SIGTERM a worker finishes its current job within
  `WORKER_SHUTDOWN_GRACE_S` or hands it back to the queue.

Workers run in one container (Compose service `worker`, same image as the API)
with a supervisor and one process per stage slot:

```sh
uv run python -m backend.worker                     # convert x1, extract x1, conflict x1
uv run python -m backend.worker --convert 1 --extract 2 --conflict 1
uv run python -m backend.worker --stage extract     # one worker, no supervisor
```

`WORKER_CONVERT_PROCESSES`/`WORKER_EXTRACT_PROCESSES`/`WORKER_CONFLICT_PROCESSES` set the default counts;
several worker containers can share the queue. Without a running worker, jobs
stay queued. The worker and API must use the same `APP_DATABASE_URL`. Conversion
covers text, PDF, DOCX, XLSX, PNG and JPEG. Scanned PDFs and images need a hosted
OCR gateway at `OCR_API_URL`; transcription of uploaded audio/video is not
implemented.
