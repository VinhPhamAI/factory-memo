# File library

Scope: the implemented personal file library. A signed-in user uploads, lists,
downloads and deletes their own files through `/v1/files` and the **Thư viện file**
page (`/files`, live mode only).

Not implemented: in-browser preview, adding library files to a knowledge base,
OCR or extraction, and access by citations or inquiries. To make uploaded documents
searchable, use the separate [document uploads](document-uploads.md) flow on the
Capture page. That flow and its `/v1/documents/{kb}/uploads` API are unchanged.

Owners: routes in `backend/api/files.py`, rules in `backend/src/services/file_library.py`,
type checks in `backend/src/services/file_types.py`, storage in
`backend/src/storage/file_library.py`, page in `frontend/src/pages/files.tsx`,
client in `frontend/src/services/file-api.ts`. Tests: `backend/tests/test_file_types.py`,
`backend/tests/test_file_library.py`, `frontend/src/**/file*.test.ts(x)`,
`frontend/e2e/files.spec.ts`.

## Endpoints

Every endpoint requires `Authorization: Bearer <access_token>` (see [authentication](authentication.md)).

| Method and path | Success | Errors |
|---|---|---|
| `POST /v1/files` (multipart field `file`, one file) | `201` metadata | `400`, `401`, `411`, `413`, `415`, `422`, `503` |
| `GET /v1/files` | `200` `{"files": [metadata, ...]}`, newest first | `401`, `503` |
| `GET /v1/files/{id}` | `200` metadata | `401`, `404`, `422` (malformed ID), `503` |
| `GET /v1/files/{id}/content` | `200` original bytes | `401`, `404`, `422`, `503` |
| `DELETE /v1/files/{id}` | `204` | `401`, `404`, `422`, `503` |

Metadata fields: `id`, `filename`, `content_type`, `size_bytes`, `sha256`, `created_at`.
The internal storage location is never returned. The list has no pagination.

| Code | Meaning |
|---|---|
| `400` | Malformed multipart body, more than one file, or more than 10 form fields |
| `401` | Missing or unusable access token |
| `404` | The file does not exist, is deleted or belongs to another user. All three return the same response, so file IDs cannot be probed |
| `411` | The upload has no `Content-Length` header, or has a `Transfer-Encoding` header (for example a chunked body) |
| `413` | Larger than 100 MiB |
| `415` | Not an accepted type, or the content does not match the extension |
| `422` | No `file` field, empty file, or a filename that is blank, longer than 255 characters, or contains control characters or bidirectional text controls (such as U+202E) |
| `503` | The file library is not configured, or the database or disk is unavailable |

Downloads send `Content-Disposition: attachment` (with the original, possibly
Vietnamese, filename), `X-Content-Type-Options: nosniff` and `Cache-Control: private, no-store`.

```sh
curl --fail-with-body -H "Authorization: Bearer $ACCESS" \
  -F 'file=@/path/to/manual.pdf' http://localhost:8005/v1/files
```

## Accepted files

A file is accepted when its extension (case-insensitive) and its first bytes
match one row. The server sets `content_type`; the browser's MIME type is ignored.

| Extension | Content type | First bytes | Also required |
|---|---|---|---|
| `.pdf` | `application/pdf` | `%PDF-` | |
| `.jpg`, `.jpeg` | `image/jpeg` | `FF D8 FF` | |
| `.png` | `image/png` | `89 50 4E 47 0D 0A 1A 0A` | |
| `.docx` | Word Open XML | `PK` ZIP header | `word/document.xml` inside the archive |
| `.xlsx` | Excel Open XML | `PK` ZIP header | `xl/workbook.xml` inside the archive |

Limits:

- 100 MiB per file. Uploads must send `Content-Length` and no `Transfer-Encoding`;
  browsers do this for form uploads. A request whose `Content-Length` exceeds the limit plus 1 MiB
  multipart overhead is refused before its body is read. The copy to disk also stops
  as soon as the limit is passed.
- The check reads signatures only. It does not detect files damaged after the first bytes.
  A PDF must start with `%PDF-` at byte 0; PDFs with leading junk bytes are refused.
- A damaged DOCX/XLSX archive is refused with `415`, never a server error.
- Identical files uploaded twice are stored as two separate files.

## Deletion

Delete is a soft delete:

1. The server sets `deleted_at` on the `files` row.
2. The file disappears from the owner's list, metadata and download (`404`).
3. The record and the bytes on disk remain.

The bytes are kept so that readers of existing citations or inquiries can still
open the original once those features exist. Read access is decided only by
`FileLibraryService.can_read`. It currently allows the owner while the file is
not deleted. When citations and inquiries are implemented, add their readers
there and retest reading a deleted file through them.

There is no cleanup of deleted files yet, so disk usage only grows.

## Storage

Bytes are stored at `<root>/<32-character hex key>`. Original filenames never
become paths. Rows are stored in the `files` table of the application database
(`APP_DATABASE_URL`); no migration beyond the existing application tables is needed.

| Runtime | Storage root |
|---|---|
| Host run (`uv run uvicorn backend.app:app ...`) | `<project>/data/files/`, or `FILE_LIBRARY_DIR` |
| Docker Compose API | `/app/data/files/`, on the named volume `file_library` |

Upload order: copy to a temporary file in the root, check the type, rename to the
final key, insert the row. The first three steps run in one worker thread and
remove their own files on failure. A failed insert removes the published bytes.

Files can remain without a row in three cases:

- The process is killed during the copy: a `.upload-*` temporary file stays in the root.
- The process stops between the rename and the insert.
- The request is cancelled (for example the client disconnects) during those steps.

Such files are unreachable and harmless. Bytes are never removed after a cancelled
insert, because the row may already be committed.

One rare case goes the other way: if the database commits the row but the connection
drops before confirming it, the insert reports a failure and the bytes are removed.
The row then stays listed, but its download returns `404`.

The volume survives container recreation and `docker compose down`;
`docker compose down -v` deletes it. Only the API service mounts it.

Without `APP_DATABASE_URL`, the API starts, but file library routes return `503`.

## Frontend

The **Thư viện file** navigation link opens `/files`:

- Upload one or more files. Each file's result appears as a popup in the bottom-right
  corner. The browser refuses unsupported extensions, empty files and files over
  100 MiB before sending them.
- Each row shows name, type, size and upload time, with **Tải về** and **Xóa**.
- Delete asks for confirmation in a dialog. A popup confirms the deletion or shows
  why it failed. If the file is already gone (`404`), its row is also removed.
- Every failed upload or delete shows a Vietnamese message in a red popup: too large,
  wrong type, file no longer exists, session expired, server unavailable, or no
  connection. A failed download shows the message under its row.
- Success popups hide after 4 seconds and error popups after 8 seconds; each has a
  close button. Popups use `frontend/src/components/ui/toast.tsx`.
- After a `401`, the client refreshes the session once and retries once. If the
  refresh fails, the sign-in screen appears.

Downloads are fetched with the access token and saved from a blob, so a whole file
(up to 100 MiB) is held in browser memory during the download.

## Tests

```sh
uv run python -m unittest backend.tests.test_file_types backend.tests.test_file_library -v
```

PostgreSQL cases need `TEST_DATABASE_URL`; without it they are skipped. The
browser smoke test needs a running API with the file library, then from `frontend/`:
`PLAYWRIGHT_SUITE=files npx playwright test`. It creates two accounts on each run.
