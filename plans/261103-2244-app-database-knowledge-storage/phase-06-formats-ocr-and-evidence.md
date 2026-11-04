# Phase 6 — Formats, OCR and located evidence

Depends on phases 1–4 and proposed decisions D6 and D7. This phase adds behavior the
current code lacks: today only plain text converts (`text_convertible`), and the
standalone OCR service (`deployments/ocr`) is not called by ingestion.

## Goal

- PDF, DOCX, XLSX, PNG and JPEG sources convert to `extracted_text`.
- Scanned PDFs and images go through OCR; text keeps its page and position.
- Every chunk link carries the most precise location found (page, region, paragraph,
  cell range or image area). It falls back to the whole source, never to a guessed location.
- OCR provider failures reach the job as a retry or a final error shown in the inventory.

## Converter boundary

New package `backend/src/services/converters/`. The convert stage selects a converter by
the type detected from file content, not by the file name.

| Converter | Input | Library | Located segment |
|---|---|---|---|
| `text` | `text/*` and the current suffixes | existing code | none (whole source) |
| `pdf_text` | PDF with a text layer | PyMuPDF (already a dependency) | page, block bbox |
| `docx` | DOCX | `python-docx` (new, D7) | paragraph index, table cell |
| `xlsx` | XLSX | `openpyxl` (new, read-only mode, D7) | sheet, cell range per row block |
| `ocr` | PNG, JPEG, PDF without a text layer | `POST {OCR_API_URL}/v1/ocr` on the hosted `ai-services-api` | page, region bbox, label |

A PDF goes to `ocr` when its text layer has fewer than `PDF_MIN_TEXT_CHARS_PER_PAGE`
characters per page on average. Mixed PDFs are converted as one unit; per-page
routing is a later optimization.

```python
@dataclass(frozen=True)
class Segment:
    """A span of extracted text and where it appears in the original file."""

    start: int  # character offsets into extracted_text
    end: int
    location: dict[str, Any]  # e.g. {"type": "page", "page": 3, "bbox": [91, 321, 365, 365]}


@dataclass(frozen=True)
class Conversion:
    """Text extracted from one file, with located segments and converter details."""

    text: str
    segments: list[Segment]
    converter: str
    details: dict[str, Any]  # page_count, OCR model, elapsed_s


class Converter(Protocol):
    def accepts(self, content_type: str, head: bytes) -> bool: ...
    async def convert(self, path: Path, content_type: str) -> Conversion: ...
```

## Storage

- `knowledge_sources.extracted_text`: joined text. Pages are separated by
  `<!-- page N -->` markers, as in the OCR service's Markdown. Sheets are separated by
  `## <sheet>` headings, with rows as tab-separated lines.
- `metadata.conversion`: `converter`, `version`, `sha256`, `at`, `details`.
- Segments: D6. The proposal is `metadata.conversion.segments` (no schema change).
- OCR figure crops (`include_images`) are not stored in this phase. Image evidence is
  the page and bbox of the `image` region on the original file.

## Located references

The extract stage maps evidence to segments:

- Knowledge mode: find each record's exact quote in `extracted_text`, then take the
  segments overlapping that span.
- Documents mode: use the span of each passage from `chunk_document`.

Reference shapes in `knowledge_chunk_sources.references`:

| `type` | Fields | Opens |
|---|---|---|
| `page` | `page`, optional `bbox`, `label`, `quote` | PDF page, region highlight |
| `paragraph` | `index`, `quote` | DOCX paragraph in the text view |
| `cells` | `sheet`, `range` (`A2:D9`), `quote` | XLSX sheet excerpt |
| `image` | `page`, `bbox` | Original image or page with the area marked |
| `source`, `passage` | as today | Whole source or passage (fallback) |

A quote that is not found exactly falls back to `source` and records
`location_missing: true`. Locations are never interpolated.

## OCR client and failures

The OCR service is the `/v1/ocr` route of a separately hosted
[`ai-services-api`](../../deployments/ai-services-api/README.md). The backend reaches
it only through an external URL from the environment, never through the Compose
service name or the AI overlay.

| Setting | Default | Meaning |
|---|---|---|
| `OCR_API_URL` | none (required for OCR) | Base URL of the hosted gateway, e.g. `https://ai.example.com`; the client appends `/v1/ocr` |
| `OCR_API_KEY` | empty | Sent as `Authorization: Bearer …` when set. The gateway has no authentication, so a URL outside loopback is expected to sit behind an authenticating proxy |
| `OCR_TIMEOUT_S` | `300` | Request timeout for one document |
| `OCR_DOC_ORIENTATION` | `0` | `1` sends `doc_orientation=true` (the host must run with `OCR_DOC_PREPROCESSING=1`) |

- `backend/src/services/converters/ocr.py`: one `httpx.AsyncClient` call per document
  (multipart `file`, `response_format=json`).
- `deployments/docker-compose.yml`: pass `OCR_API_URL`, `OCR_API_KEY`,
  `OCR_TIMEOUT_S` and `OCR_DOC_ORIENTATION` to the `worker` service as they are, with
  no internal default. The convert stage runs in the worker; the API only reports
  `can_convert`.
- `OCR_API_URL` unset: files that need OCR fail without retry with
  "Chưa cấu hình OCR_API_URL" ("OCR_API_URL is not configured"). Text, PDF-with-text,
  DOCX and XLSX conversion still work. The worker logs one warning at startup.
- A non-HTTPS URL outside loopback logs a warning, because the original file is sent
  over the network.
- Never log `OCR_API_KEY`, the file contents or the OCR text.
- Failure mapping:

  | Failure | Job outcome | Message |
  |---|---|---|
  | Connection error, timeout, 503 (queue full or VLM loading), 5xx | Retry with backoff; source stays `processing` | `OCR service unavailable` (no response body) |
  | 400/413/422 (unsupported, too large, too many pages) | Final failure, no retry | The service limit, e.g. the page limit |
  | Final attempt reached | Source `failed`; job `error` shown in the inventory | Last message |

- Record `vlm_model`, `pipeline`, `page_count` and `elapsed_s` in
  `metadata.conversion.details`. Never log the file contents or the OCR text.

## Size limits

- Knowledge mode keeps `MAX_TEXT_CHARS`. The existing message suggests documents
  mode for longer files.
- The upload size limit stays. The OCR page limit (`OCR_MAX_PAGES`) is reported as a
  final failure before the request is sent, when the page count is known.

## UI (`frontend/src/components/evidence-panel.tsx`, `source-preview.tsx`)

- `page`: open the original PDF at `#page=N`. Draw the bbox over the rendered page
  image when one is provided.
- `image`: show the original image with the area marked.
- `paragraph`, `cells`: show the segment text from `extracted_text`, with the sheet and
  range or the paragraph number.
- `source` with `location_missing`: show "Không xác định được vị trí" ("Location not
  found") and the quote.
- Source detail returns `segments` for the selected reference only, not the whole list.

## Affected files

- New: `backend/src/services/converters/{__init__,text,pdf,docx,xlsx,ocr}.py`.
- `backend/src/services/source_pipeline.py` (convert stage, reference mapping),
  `backend/src/storage/document_uploads.py` (remove `text_convertible` after moving it).
- `backend/src/services/application_knowledge_management.py` (`can_convert`,
  `conversion_note`, detail references).
- `pyproject.toml`, `uv.lock` (`python-docx`, `openpyxl`); `.env.example` (`OCR_API_URL`, `OCR_API_KEY`, `OCR_TIMEOUT_S`, `OCR_DOC_ORIENTATION`), `deployments/docker-compose.yml` (worker environment).
- Frontend evidence components and their tests.
- Docs: `docs/features/document-uploads.md`, `docs/features/knowledge-management.md`,
  `deployments/ocr/README.md` (ingestion now calls it).

## First failing tests

Fixtures in `backend/tests/fixtures/formats/`, one per format: `manual.pdf` (text
layer), `scanned.pdf` and `label.png` (no text layer), `manual.docx`,
`fault_codes.xlsx`. Each holds a few Vietnamese lines and one table.

- Each converter returns the expected text and segments for its fixture.
- `ocr`: a stub transport returns the recorded OCR JSON for a request to `OCR_API_URL` + `/v1/ocr`, with the bearer header only when `OCR_API_KEY` is set. With `OCR_API_URL` unset, OCR files fail without retry and other formats convert. The conversion keeps pages,
  region order and bbox. A 503 leads to a retry; a 413 is a final failure with the
  limit message.
- A recorded quote maps to `page`/`paragraph`/`cells`; a missing quote maps to `source`
  with `location_missing`.
- Documents mode: a passage spanning two pages gets two `page` references.
- The UI opens each reference type; the fallback shows the "location not found" notice.

## Completion checks

- With `OCR_API_URL` pointing to a hosted `ai-services-api` that serves OCR: upload `scanned.pdf`, convert, extract in both
  modes, and open a cited page with its region marked.
- Point `OCR_API_URL` to an unreachable host, or stop the hosted OCR worker, during conversion: the job retries, then fails with the message
  shown in the inventory.
- Offline converter and pipeline tests pass. Frontend build, lint and touched tests pass.
- OCR accuracy on real factory documents remains unevaluated; state this in the docs.
