# Document uploads

The legacy `/capture` page, hidden from main navigation, has a **Tải lên tài liệu** tab. Select a knowledge base,
one or more files, and **Chỉ lưu** or **Auto · chạy đến Extract**. It uses the
same `uploadFile` client and `POST /v1/files` API as **Thư viện tài liệu**, then
attaches the saved file ID through `POST /v1/documents/{kb}/files`.
The original bytes are stored once in the personal library and are also read by
KB processing. Files appear in the owner's library. If attachment fails, the
file remains there; retrying attachment does not upload its bytes again.

This tab accepts PDF, DOCX, XLSX, PNG and JPEG, with the same signature checks
and 100 MiB limit as the [file library](file-library.md). **Chỉ lưu** creates a
raw KB source for later processing. **Auto** queues conversion and extraction.
Extracted knowledge remains searchable as drafts, pending human review.

The **Thêm tài liệu** control in **Quản lý tri thức** and existing API clients
still use the direct upload endpoint described below. Existing originals stay
in their original storage location.

Upload always stores the original file first. Processing is separate: TXT and
other text files, PDF, DOCX, XLSX, PNG and JPEG can then be converted and
extracted. Scanned PDFs and images require the hosted OCR gateway. Other formats,
including PPTX and uploaded audio/video, remain stored as opaque bytes. Each file
must be nonempty and at most **100 MiB**. The worker detects supported formats
from their bytes; the client-supplied MIME type is metadata, not proof of format.

## Storage

| API runtime | Storage root |
| --- | --- |
| Capture tab, host run | `<project>/data/files/`, or `FILE_LIBRARY_DIR` |
| Capture tab, Docker API and worker | `/app/data/files/`, named volume `file_library` |
| Direct KB uploads, host run | `<project>/data/uploads/`, independent of the shell's working directory |
| Direct KB uploads, Docker API and worker | `/app/data/uploads/`, named volume `document_uploads` |

`DOCUMENT_UPLOAD_DIR` overrides the host-run location; Compose explicitly sets it
to the volume mount. Storage follows the **API server's runtime**, so a `curl`
request to the Docker API also writes to its volume. No separate S3 service is
needed: this is a local filesystem object store backed by that volume. The
volume survives container recreation and `docker compose down`; `down -v`
removes it. Existing audio still uses the host bind mount `data/audio/`.

Library objects use flat 32-character hexadecimal keys. API and workers resolve
these keys under `FILE_LIBRARY_DIR`; both mount the same `file_library` volume.
Direct-upload objects retain their existing layout:

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

`POST /v1/documents/{kb}/files` accepts JSON
`{"file_id":"<uuid>","extract_mode":null}`. `extract_mode` may instead be
`knowledge` or `documents` to queue Auto processing. The caller must own the
non-deleted personal file, and its bytes must exist. It creates only a
`knowledge_sources` row referencing the existing `files` row; bytes are not copied.
It returns `201` with the source metadata, or `200` and `duplicate: true` for an
identical active source in the KB. Duplicates do not queue more work. Errors:
`401` for missing authentication, `404` for an unavailable KB/file or another
owner's file, `422` for invalid IDs/modes, and `503` for unavailable storage.
Queue failure returns the stored source with `job_error`, as for direct uploads.

Deleting the personal library entry does not retract already attached KB evidence.
Removing a KB source does not delete its personal library entry. Original downloads
and located evidence previews continue to resolve the same stored bytes.

`POST /v1/documents/{kb}/uploads` accepts one multipart **file** field per request
and an existing application KB name. A bearer access token is required. It
returns `201` with the new source ID and saved file metadata. An identical active
file in the same KB returns `200` with the existing source ID, file metadata and
`duplicate: true`. Duplicate uploads discard the extra copy of the bytes and leave
the existing source's jobs as they are. After the source is removed, the same bytes
can be uploaded and extracted as a new source. The removed source retains its
original file and extraction history.

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

**Auto mode.** Capture sends `extract_mode` in the attachment JSON. Direct-upload
clients may send the form field `extract_mode` (`knowledge` or `documents`).
The file is stored first, then a convert → extract pipeline is queued for the
[job workers](knowledge-management.md#job-queue-and-workers); the response adds
`job`. If nothing can be queued (no converter for the file type, or the queue is
unavailable), the response keeps the stored upload and adds `job_error`; the file
stays raw. Without `extract_mode` (**Chỉ lưu**) the upload stays raw as before.
The legacy `POST /v1/voice/{kb}/texts` remains available for existing conversation
clients; the document tab never calls it.
