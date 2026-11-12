# File library

Scope: the implemented personal file library. A signed-in user uploads, lists,
downloads and deletes their own files through `/v1/files` and the **Thư viện tài liệu**
page (`/files`, live mode only).

The page uses the administrator-selected default KB without a selector. An
administrator must select a default before uploading. Each new file is stored through
`/v1/files`, then attached through `POST /v1/documents/{kb}/files` with knowledge
extraction requested. The worker converts and extracts it asynchronously. A
queue or attachment failure leaves the personal file available and is shown on
the row; a failed attachment can be retried without uploading the bytes again.
An identical active KB source is linked instead of starting a duplicate job.
The library matches each file to sources by its file record ID and refreshes source
state in the background, including after page reload. Matching by record ID keeps
separate uploads of identical bytes isolated between KBs. The grid lists only files
with a source in the default KB. A file stored or failing to attach in the current page
session also stays listed so its state and retry remain reachable. Files that belong only to
other KBs, or to no KB, are not shown; they remain in the personal library and
`GET /v1/files` still returns them. The card menu previews,
opens or downloads the file, shows processing state or removes the source from the
KB after confirmation.
Multiple cards can be selected for source removal. One library file can be attached
to several KBs, but separately uploaded files remain separate even when their hashes
match. Source removal keeps the personal file and retains the evidence for history;
the file then no longer appears in this KB's library. A running job blocks
removal, and backend role and ownership rules apply. Failed attachments can be
retried from **Trạng thái xử lý** in the current page session. Previews use
authenticated endpoints.

The legacy Capture page remains available by URL but is no longer in the main
navigation. Its upload flow can still save without processing; see
[document uploads](document-uploads.md).

Expert inquiries read their attached files through their own route; see the Deletion section.

Both flows use the application `files` table. Personal files have flat,
32-character hexadecimal storage keys under `FILE_LIBRARY_DIR`. KB sources attached
from the library reference these same file rows and bytes. Direct KB uploads retain
nested keys under `DOCUMENT_UPLOAD_DIR` and are excluded from `/v1/files`.

The API can start without `APP_DATABASE_URL`; database-backed endpoints then
return unavailable responses. A missing or incompatible KB embedding setting
disables KB routes while authentication, conversations and the personal library
remain configured. Correct the KB setting and restart the API to enable KB routes.

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
| `GET /v1/files/{id}/preview` | `200` PNG thumbnail or text excerpt | `401`, `404`, `422`, `503` |
| `DELETE /v1/files/{id}` | `204` | `401`, `404`, `422`, `503` |

Metadata fields: `id`, `filename`, `content_type`, `size_bytes`, `sha256`, `created_at`.
The internal storage location is never returned. The list has no pagination.
Preview requires the same ownership as download and uses `private, no-store`.
PDF and images render the first page as a PNG up to 480 pixels on the long
edge. DOCX previews contain up to 30 paragraphs, XLSX previews contain up
to 15 rows and 8 columns from the active sheet, and TXT/Markdown previews
contain the start of the file as plain text (not rendered Markdown); all are
limited to 4,000 characters. Corrupt or unreadable files return `422`.

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
| `.txt` | `text/plain` | none | Whole file decodes as UTF-8 (BOM allowed) or BOM-marked UTF-16, with no NUL characters |
| `.md` | `text/markdown` | none | Same as `.txt` |

Limits:

- 100 MiB per file. Uploads must send `Content-Length` and no `Transfer-Encoding`;
  browsers do this for form uploads. A request whose `Content-Length` exceeds the limit plus 1 MiB
  multipart overhead is refused before its body is read. The copy to disk also stops
  as soon as the limit is passed.
- PDF and image checks read signatures only. They do not detect files damaged after the first bytes.
  Text files are decoded in full, so a Latin-1 or otherwise non-UTF-8 text file is refused with `415`.
  A PDF must start with `%PDF-` at byte 0; PDFs with leading junk bytes are refused.
- A damaged DOCX/XLSX archive is refused with `415`, never a server error.
- Identical files uploaded twice are stored as two separate files.

## Deletion

Delete is a soft delete:

1. The server sets `deleted_at` on the `files` row.
2. The file disappears from the owner's list, metadata and download (`404`).
3. The record and the bytes on disk remain. Already attached KB sources stay readable
   and processable. New attachment requires an active personal file.
4. Inquiries that attached the file keep it. The requester and the assignee of a
   submitted inquiry can still download it through
   `GET /v1/inquiries/{id}/files/{file_id}/content`. Other users get `404`
   ([expert inquiries](inquiries.md#evidence-after-the-library-file-is-deleted)).

Library reads are decided by `FileLibraryService.can_read`. It allows only the owner
while the file is not deleted. Inquiry reads are decided by
`InquiryStore.get_attached_file` in `backend/src/storage/inquiries.py`, which does not
check `deleted_at`. Citation readers are not implemented yet; when they are, add
them to `can_read` and retest reading a deleted file through them.

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
`docker compose down -v` deletes it. API and worker mount it so that KB processing
reads the same library bytes. Removing a KB source does not hide its library entry.

Without `APP_DATABASE_URL`, the API starts, but file library routes return `503`.

## Frontend

The **Thư viện tài liệu** navigation link opens `/files`:

- Drag files onto the upload area or click **chọn từ máy** to upload one or more
  files to the default KB. Each new
  source requests knowledge processing. Upload and queue success appear as a
  popup; attachment, duplicate and queue problems appear on the file row. The
  browser refuses unsupported extensions, empty files and files over 100 MiB
  before sending them.
- The grid shows thumbnails, name, type, size and upload time. **Tìm tài liệu**
  filters filenames locally. Hover or focus a card to select it or open its menu.
  Thumbnail requests start near the viewport and report when unavailable.
- Clicking a filename or thumbnail, or **Xem trước** in the card menu, opens a
  preview dialog. It is not a full document viewer:

  | File | Dialog shows | Actions |
  |---|---|---|
  | PDF | Bounded first-page preview (**Trang đầu**) | **Mở PDF**, **Tải xuống** |
  | PNG/JPEG up to 12 MiB | Original bytes (**Ảnh gốc**) | **Tải xuống** |
  | PNG/JPEG over 12 MiB | Bounded preview (**Ảnh thu nhỏ**) | **Mở ảnh gốc**, **Tải xuống** |
  | DOCX/XLSX/TXT/MD | Server text excerpt (**Trích đoạn**), partial content only | **Tải xuống** |

  A `422` preview response shows that the file has no preview; it can still be
  downloaded.
- **Mở tệp gốc** opens PDF and images in a new tab; Office and text files download because
  browsers do not natively display them. **Tải xuống** saves the original.
  Each card shows the latest processing status and a **Xem chi tiết** link to the
  source details; the menu also has **Mở chi tiết tri thức** (one entry per KB when
  the file has sources in several KBs). **Trạng thái xử lý** shows the same status
  in a dialog. **Gỡ khỏi kho kiến thức** asks for confirmation; it does not delete the
  library file. Removal remains disabled when no matching source is known.
- Upload failures use a popup; queue errors appear on the card. Failed source
  removals use a popup and leave the source intact.
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
