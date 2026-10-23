# Document uploads

The `/capture` page has a **Tải lên tài liệu** tab, replacing the typed-text
capture form. Select a knowledge base and one or more files. The tab has no
conversation name, speaker settings or extraction controls. Successful uploads
show their object IDs; retries send only files that failed in the current batch.

This feature stores original files only. It does not create conversations,
parse/OCR documents, extract knowledge, embed content or make it searchable.
PDF, images, TXT, PPTX, DOCX, XLSX and other file types are accepted as opaque
bytes, independent of the KB's retrieval mode. Each file must be nonempty and
at most **100 MiB**. MIME types are client-supplied metadata, not validated formats.

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
<root>/<kb>/<uuid>/metadata.json
```

Metadata retains the object ID, KB, original filename, client content type, byte
size, SHA-256, relative storage key, upload time (UTC) and `status: "stored"`.
Original filenames never become paths; repeated filenames create separate UUID
objects. File and metadata are written in a temporary directory, then published
together with a directory rename. Failed writes are cleaned up. Metadata persists
beside the file without a database write; no LLM, embedding or speech calls occur.

## API / CLI

`POST /v1/documents/{kb}/uploads` accepts one multipart **file** field per request
and an existing KB name. It returns `201` with the saved metadata, `404` for an
unknown KB, `422` for missing/empty files or invalid filenames, `413` for a file
over the limit and `503` when storage fails. Clients can submit multiple requests
for a batch. As with the existing API, no authentication is configured.

```sh
curl --fail-with-body \
  -F 'file=@/path/to/manual.pdf' \
  http://localhost:8005/v1/documents/general/uploads
```

The response's `storage_key` is relative to the storage root, for example
`general/0123456789abcdef0123456789abcdef/content`. The upload response confirms
storage only. There is currently no document listing, download or extraction API.
The legacy `POST /v1/voice/{kb}/texts` remains available for existing conversation
clients; the document tab never calls it.
