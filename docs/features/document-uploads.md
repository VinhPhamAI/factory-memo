# Document uploads

The `/capture` page has a **Tải lên tài liệu** tab, replacing the typed-text
capture form. Select a knowledge base and one or more files. The tab has no
conversation name, speaker settings or extraction controls. Successful uploads
show their object IDs; retries send only files that failed in the current batch.

Upload always stores the original file first. Processing is separate: TXT and
other text files, PDF, DOCX, XLSX, PNG and JPEG can then be converted and
extracted. Scanned PDFs and images require the hosted OCR gateway. Other formats,
including PPTX and uploaded audio/video, remain stored as opaque bytes. Each file
must be nonempty and at most **100 MiB**. The worker detects supported formats
from their bytes; the client-supplied MIME type is metadata, not proof of format.

## Storage

| API runtime | Storage root |
| --- | --- |
| Direct host run, e.g. `uv run uvicorn backend.app:app --port 8005` | `<project>/data/uploads/`, independent of the shell's working directory |
| Docker Compose API | `/app/data/uploads/`, mounted from the Docker-managed named volume `document_uploads` (normally `knowledge-agent_document_uploads`) |

`DOCUMENT_UPLOAD_DIR` overrides the host-run location; Compose explicitly sets it
to the volume mount. Storage follows the **API server's runtime**, so a `curl`
request to the Docker API also writes to its volume. No separate S3 service is
needed: this is a local filesystem object store backed by that volume. The
volume survives container recreation and `docker compose down`; `down -v`
removes it. Existing audio still uses the host bind mount `data/audio/`.

Objects are stored as:

```text
<root>/<kb>/<uuid>/content
```

The `files` and `knowledge_sources` application tables retain the source ID,
KB, original filename, client content type, byte size, SHA-256, storage key,
submitter and timestamps.
Original filenames never become paths; repeated filenames create separate UUID
objects. Bytes are written in a temporary directory and then published with a
directory rename. If the database transaction fails, the published object is
removed. No LLM, embedding or speech calls occur during storage.

## API / CLI

`POST /v1/documents/{kb}/uploads` accepts one multipart **file** field per request
and an existing application KB name. A bearer access token is required. It
returns `201` with the new source ID and saved file metadata. An identical active
file in the same KB returns `200` with the existing source ID, file metadata and
`duplicate: true`. Duplicate uploads discard the extra copy of the bytes and leave
the existing source's jobs as they are. After the source is removed, the same bytes
can be uploaded as a new source.

Errors include `401` for an unusable token, `404` for an unknown or deleted KB,
`422` for missing or empty files or invalid filenames, `413` for a file over the
limit and `503` when storage fails. Clients can submit multiple requests for a batch.

```sh
curl --fail-with-body \
  -H 'Authorization: Bearer <access-token>' \
  -F 'file=@/path/to/manual.pdf' \
  http://localhost:8005/v1/documents/<kb>/uploads
```

The response's `storage_key` is relative to the storage root, for example
`general/0123456789abcdef0123456789abcdef/content`. The upload response confirms
storage only. Listing, original downloads, text conversion, per-source extraction
and human review are available separately in **Quản lý tri thức**; see the
[management guide](knowledge-management.md). Supported files can be extracted
directly without a manual Convert step. PDF files with a sufficient text layer
are read locally. PNG, JPEG and scanned PDFs use `OCR_API_URL`; OCR of embedded
Office images is not implemented.

**Auto mode.** With **Auto · chạy đến Extract** selected on the capture page, the
upload also sends the form field `extract_mode` (`knowledge` or `documents`).
The file is stored first, then a convert → extract pipeline is queued for the
[job workers](knowledge-management.md#job-queue-and-workers); the response adds
`job`. If nothing can be queued (no converter for the file type, or the queue is
unavailable), the response keeps the stored upload and adds `job_error`; the file
stays raw. Without `extract_mode` (**Chỉ lưu**) the upload stays raw as before.
The legacy `POST /v1/voice/{kb}/texts` remains available for existing conversation
clients; the document tab never calls it.
