# Knowledge management

The **Quản lý tri thức** tab (`/knowledge`) is the shared source inventory and
review workspace. **Thêm dữ liệu** (`/capture`) only captures microphone audio or
uploads original files. It links to management after saving. Existing
`/capture/<kb>/<conversation_id>` bookmarks redirect to the shared workspace.

## Source lifecycle

Processing and human review are independent:

| Stage | Meaning |
| --- | --- |
| `raw` | The original upload is stored. This does not imply that it has been read, OCRed or indexed. |
| `converted` | Text is available: a microphone transcript, an existing text evidence record, or a strictly decoded text upload. |
| `extracted` | Knowledge items or document chunks have been generated. Results still need human review. A completed run can also contain zero new items, e.g. consolidation only added support. |

TXT uploads can go **directly from raw to extraction**. The extraction request
reads UTF-8 (optionally with BOM) or BOM-marked UTF-16, preserves line endings,
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

Extraction runs in the background, records status and the selected mode, and
surfaces failures or interruptions. Retrying reuses the evidence ID. A source
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
The UI does not implement user authentication or multi-worker job coordination.

All endpoints below are under `/v1/knowledge-management/{kb}`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/sources?offset=0` | Source inventory and partial-availability warnings |
| GET | `/sources/{kind}/{id}` | Source, text preview, processing and review results |
| GET | `/sources/upload/{id}/original` | Download original bytes |
| POST | `/sources/upload/{id}/convert` | Decode a supported text file |
| POST | `/sources/{kind}/{id}/extract` | `{"mode":"knowledge"}` or `{"mode":"documents"}`; TXT can skip Convert |
| POST | `/knowledge/{record_id}/review` | Knowledge decision and optional note |
| POST | `/sources/{kind}/{id}/review-chunk` | Source-scoped chunk decision, chunk ID and optional note |

Kinds are `upload`, `conversation` (existing evidence-table IDs) and `document`
(URL-safe token for an indexed document ID). The legacy voice review endpoints
remain available for existing clients. Text previews are limited to 100,000
characters; document chunk processing still reads the whole converted text.
