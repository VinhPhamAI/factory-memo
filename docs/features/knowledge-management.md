# Knowledge management

The **Quản lý tri thức** tab (`/knowledge`) is the shared source inventory and
review workspace. **Thêm dữ liệu** (`/capture`) only captures microphone audio or
uploads original files. It links to management after saving. Its **Auto · chạy
đến Extract** option queues the whole pipeline at save time instead (see
[Job queue and workers](#job-queue-and-workers)). Existing
`/capture/<kb>/<conversation_id>` bookmarks redirect to the shared workspace.

## Source lifecycle

### Conflict review

Select a KB in **Quản lý tri thức**, then open **Mâu thuẫn**, alongside
**Nguồn dữ liệu**. The default filter is suspected conflicts (`open`); the status
selector also exposes pending judgements, evidence needing review, confirmed,
dismissed, resolved and non-conflicting pairs. Results are paginated. KBs without
conflict detection show an explanatory empty state.

Each pair shows both full claims, their record review states, the judge's quoted
passages, relation, aspect, confidence and confirmation/link state. Expand
**Phạm vi và bằng chứng nguồn** for scope, validity dates when present, verbatim
source evidence and links to the source. Notes and previous review decisions
remain visible in the review history.

- **Xác nhận mâu thuẫn** adds conflict links; it does not approve either record.
- **Bỏ qua** dismisses the pair and withdraws conflict links, without approving
  either record.
- **Thay thế một bản ghi…** requires explicitly selecting the survivor in a
  dialog. The other record is retired from search and kept in history. The
  survivor keeps its existing review status.

Missing records and resolved pairs cannot be reviewed. If a record changed since
the judgement (`stale`), review controls are disabled until it is compared again.
**Kiểm tra lại cặp này** queues comparison for that pair's evidence sources;
**Kiểm tra lại bản ghi đã đổi** asks the backend to queue changed records. These
are queued jobs, not immediate LLM results, and require a conflict worker.

The source list and source detail show conflict job progress/failure separately
from extraction. Management keeps polling while loaded sources have active
ingestion/conflict jobs; the conflict list refreshes while those jobs run and when
their state changes. **Làm mới mâu thuẫn** also fetches the current list manually.
Source-level counts describe the last detection run, not current review totals;
the conflict filter counts come from the review API.

The UI uses `GET /v1/knowledge-management/{kb}/conflicts`,
`POST /v1/knowledge-management/{kb}/conflicts/{id}/review` and
`POST /v1/knowledge-management/{kb}/conflicts/recheck`. Frontend unit tests and
Playwright checks use mocked API responses; they do not validate a live
database/LLM deployment.

### Processing and review

Processing and human review are independent:

| Stage | Meaning |
| --- | --- |
| `raw` | The original upload is stored. This does not imply that it has been read, OCRed or indexed. |
| `converted` | Text is available: a microphone transcript, an existing text evidence record, or a strictly decoded text upload. |
| `extracted` | Knowledge items or document chunks have been generated. Results still need human review. A completed run can also contain zero new items, e.g. consolidation only added support. |

TXT uploads can go **directly from raw to extraction**. In the source list, check
one or more sources (or **select all** visible ones) and use **Convert** or
**Extract** in the bar above the list; Extract on a raw source queues conversion
followed by extraction. Each button applies only to the checked sources that
support that step and shows how many; running sources and sources without a
converter cannot be checked. Sources whose request fails stay checked for a retry. Conversion reads UTF-8 (optionally with BOM) or BOM-marked UTF-16, preserves line endings,
and saves a converted-text snapshot automatically. No manual Convert action is
required. Invalid encodings, empty text and NUL-containing text are refused;
original bytes remain intact. Other text formats (MD, CSV, TSV, JSON, XML, YAML,
LOG or `text/*`) have an explicit **Chuyển thành text** action. No OCR or generic
media converter is implemented: PDFs, images, Office files and uploaded media
stay raw with an explanatory message. Microphone recordings already have ASR
transcripts and therefore start in converted.

The inventory combines uploads, existing source evidence in `raw_conversations`
and existing indexed documents within the selected KB. Uploads and their linked
text evidence appear once. Database and chunk-index list failures are shown as
warnings, so the available original uploads remain visible. Source/index lists
have a **Tải thêm** button; filters and counts operate on loaded entries.
Unlinked interrupted audio files have no KB/source registry entry and remain
outside this inventory. Original uploads can be downloaded as attachments.

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
| POST | `/sources/upload/{id}/convert` | Queue decoding of a supported text file (202 `{job}`) |
| POST | `/sources/{kind}/{id}/extract` | `{"mode":"knowledge"}` or `{"mode":"documents"}`; queues convert → extract for a raw upload (202 `{status, mode, job}`) |
| POST | `/knowledge/{record_id}/review` | Knowledge decision and optional note |
| POST | `/sources/{kind}/{id}/review-chunk` | Source-scoped chunk decision, chunk ID and optional note |

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

## Duplicate content

Extracting **exactly** the same content twice in the same mode is blocked;
content that is **nearly** the same as an extracted source needs the reviewer's
confirmation (see [near-duplicate content](#near-duplicate-content)).
Re-extraction of identical content with changed logic is not implemented.

- **Content fingerprint:** SHA-256 of the evidence text after Unicode NFC and
  whitespace collapse, so re-uploads that differ only in line endings or spacing
  match. Case, punctuation and wording changes are different content. Before
  conversion a raw upload is compared by the SHA-256 of its original bytes.
- **Extraction fingerprint:** SHA-256 of the mode and its settings: extraction
  provenance (pipeline version, prompt, model and schema/ontology hashes) for
  knowledge items, chunking/index settings for document chunks.
- **`extraction_runs`** (in the KB database) records each extraction of an
  evidence row: content and extraction fingerprints, original-byte hash, job ID,
  status (`running`, `done`, `failed`, `duplicate`) and result counts. A unique
  partial index allows one `running`/`done` run per content, mode and
  fingerprint; a transaction lock on content + mode serializes starts across
  different fingerprints.

Policy:

| Earlier run of the same content and mode | Effect |
| --- | --- |
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
and can be extracted later if the earlier run fails. A requeued job of the same
source takes over its own running run. Runs have no heartbeat, so set
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

Each stage is a job row in `ingestion_jobs` in the `DATABASE_URL` database
(shared by all KBs). The pattern is notify-consume:

```text
upload (Auto) / Extract on raw ─► convert job ─ NOTIFY ─► convert worker
                                       │ done: next job in the same transaction
                                       ▼
          Extract on converted ─► extract job ─ NOTIFY ─► extract worker ─► draft items/chunks
                                       │ knowledge mode, KB with conflict detection
                                       ▼
                                  conflict job ─ NOTIFY ─► conflict worker ─► knowledge_conflicts
```

The conflict stage runs only for knowledge-mode extraction in a KB that enables
it (currently `industrial_qa`, `[conflicts] enabled`). It compares the new items
with similar stored items and records suspected conflicts for review; it never
changes item status. Its job is reported separately (`processing.conflict_job`,
summary in `processing.conflicts`) and does not change the source's processing
state. See [industrial QA conflict detection](conflict-detection.md).

- Enqueueing inserts the job and calls `pg_notify` on the stage's channel in one
  transaction. Workers `LISTEN`, claim with `FOR UPDATE SKIP LOCKED` and also
  poll (`WORKER_POLL_S`, default 15 s), so a missed notification only delays a job.
- A job lists the stages that follow it. Completing it creates the next job in
  the same transaction, so a pipeline neither stops between stages nor runs a
  stage twice. A failed convert queues no extraction.
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
stay queued. Conversion covers plain-text uploads only; OCR and speech-to-text
for uploaded media are not implemented, so such files stay raw. Legacy voice API
clients that do not send `extract_mode` still extract in the API process and
then queue the conflict job.
