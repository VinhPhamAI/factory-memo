# Knowledge management

The **Quản lý tri thức** tab (`/knowledge`) is the shared source inventory and
review workspace. **Thêm dữ liệu** (`/capture`) only captures microphone audio or
uploads original files. It links to management after saving. Its **Auto · chạy
đến Extract** option queues the whole pipeline at save time instead (see
[Job queue and workers](#job-queue-and-workers)). Existing
`/capture/<kb>/<conversation_id>` bookmarks redirect to the shared workspace.

## Source lifecycle

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
Decisions append time, note and an unauthenticated actor to history. Review does
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

Uploads keep `content` and immutable `metadata.json`; processing adds an atomic
`processing.json` and `converted.txt` in the same object directory and Docker
volume. Evidence and knowledge records use existing KB database tables. The
`document_upload` evidence kind distinguishes an uploaded document from a
conversation; it has no invented speakers or dialogue turns. Chunk metadata
retains evidence IDs, original upload IDs, transcript hashes and review history.
The UI does not implement user authentication.

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
next stages, attempts, error and retry time).

## Job queue and workers

Each stage is a job row in `ingestion_jobs` in the `DATABASE_URL` database
(shared by all KBs). The pattern is notify-consume:

```text
upload (Auto) / Extract on raw ─► convert job ─ NOTIFY ─► convert worker
                                       │ done: next job in the same transaction
                                       ▼
          Extract on converted ─► extract job ─ NOTIFY ─► extract worker ─► draft items/chunks
```

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
uv run python -m backend.worker                     # convert x1, extract x1
uv run python -m backend.worker --convert 1 --extract 2
uv run python -m backend.worker --stage extract     # one worker, no supervisor
```

`WORKER_CONVERT_PROCESSES`/`WORKER_EXTRACT_PROCESSES` set the default counts;
several worker containers can share the queue. Without a running worker, jobs
stay queued. Conversion covers plain-text uploads only; OCR and speech-to-text
for uploaded media are not implemented, so such files stay raw. Legacy voice API
clients that do not send `extract_mode` still extract in the API process.
