# Brainstorm: Personal file library

Date: 2026-11-03. Status: approach agreed; no detailed plan yet.
Base commit: `a5ef5f5` (merge of PR #7, sign-in and sessions).

## 1. Goal

A signed-in user has a private file library. In it, the user can:

- Upload PDF, XLSX, DOCX, JPEG or PNG files, up to 100 MiB each.
- List their uploaded files and view each file's details.
- Download a file.
- Delete a file from the library.

Users see and act only on their own files.

## 2. Out of scope

| Item | Reason |
|---|---|
| In-browser preview | Decided not to build yet. Download only |
| Adding files to a knowledge base, OCR, extraction | Handled by the existing Capture and Knowledge management flows |
| Special admin permissions | Only the file owner manages a file |
| Citations and inquiries | Not yet built on the new database. This plan only prepares an extension point (section 6) |
| Changes to the legacy upload API | Kept as is (see section 3) |

## 3. Current state

| Component | State |
|---|---|
| Sign-in | Exists. The API checks access tokens through the `require_current_user` dependency |
| `files` table | Migration exists. Columns: `filename`, `content_type`, `size_bytes`, `storage_key`, `sha256`, `uploaded_by`, `deleted_at`. No service writes to it yet |
| `inquiry_files` table, `knowledge_sources.file_id` column | Exist and reference `files.id`. No service uses them yet |
| Legacy upload API `POST /v1/documents/{kb}/uploads` | Stores files per knowledge base in `data/uploads/`. No sign-in required. Accepts any file type. Used by the Capture page, the Knowledge management page and the job worker |
| Frontend | Has an API client that attaches the access token. No file library page yet |

**Decision:** build a new API under `/v1/files`, separate from the legacy API. The three
consumers of the legacy API are not affected.

## 4. Proposed API

Every endpoint requires the header `Authorization: Bearer <access_token>`.

| Method and path | Action | Success |
|---|---|---|
| `POST /v1/files` | Upload one file (multipart, field `file`) | `201` with metadata |
| `GET /v1/files` | List my files, newest first | `200` |
| `GET /v1/files/{id}` | Metadata of one file | `200` |
| `GET /v1/files/{id}/content` | Download the original file | `200` with the bytes |
| `DELETE /v1/files/{id}` | Remove from the library | `204` |

Returned metadata: `id`, `filename`, `content_type`, `size_bytes`, `sha256`, `created_at`.
`storage_key` is not returned, because it is an internal storage location.

**Error codes**

| Code | When |
|---|---|
| `401` | Token missing or unusable |
| `404` | File does not exist, was deleted, or belongs to another user. One shared code, so other users cannot tell whether a file exists |
| `413` | File larger than 100 MiB |
| `415` | Unsupported file type, or content does not match the file extension |
| `422` | No file, empty file, or invalid filename |
| `503` | Disk or database write failed |

Downloads send `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`.
These headers make the browser save the file instead of opening or running it.

## 5. File type validation

A file is valid when its **extension** and its **content** match the same row below.
The server sets `content_type` from that row. The MIME type sent by the browser is not trusted.

| Type | Extension | First bytes | Extra check |
|---|---|---|---|
| PDF | `.pdf` | `%PDF-` | |
| JPEG | `.jpg`, `.jpeg` | `FF D8 FF` | |
| PNG | `.png` | `89 50 4E 47 0D 0A 1A 0A` | |
| DOCX | `.docx` | `PK\x03\x04` (ZIP) | Contains `word/document.xml` |
| XLSX | `.xlsx` | `PK\x03\x04` (ZIP) | Contains `xl/workbook.xml` |

Examples rejected with `415`: an `.exe` renamed to `.pdf`, a plain ZIP renamed to `.docx`.

**Options considered**

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Extension only | Simplest | Renaming the file bypasses it | Rejected |
| Extension + first bytes, using the Python standard library (`zipfile`) | No new dependency; enough for 5 types | Does not detect files corrupted in the middle | **Chosen** |
| `python-magic` library | Detects many types | Needs `libmagic` installed on hosts and in the Docker image | Rejected; only 5 types are needed |

The 100 MiB limit is checked while reading the upload stream. The server stops as soon as
the limit is exceeded, without reading the rest of the file.

## 6. File deletion

**Approach:** soft delete. The server sets `deleted_at` on the record and keeps the bytes on disk.

- A deleted file disappears from the owner's list, metadata and download (`404`).
- Deleting an already deleted file also returns `404`.
- The bytes remain, so readers of existing citations or inquiries can still open the original later.

**Options considered**

| Option | Verdict |
|---|---|
| Hard delete of bytes and record | Rejected. Existing citations and inquiries would point to a missing file |
| Soft delete, keep bytes | **Chosen**. The `files` table already has `deleted_at` |
| Soft delete, then remove bytes after N days when nothing references the file | Later. Needs real citations and inquiries to know when nothing references a file |

**Extension point for citations and inquiries**

Every "who may read this file" decision goes through **one function**, for example `can_read(file, user)`.

- Now: only the owner, and only while the file is not deleted.
- Later: add a branch for "the user may read an inquiry or citation that references this file".
  That branch can still read deleted files.

When citations and inquiries are built, test this reader path again. Record that in the
"Next steps" of the related plan.

## 7. Storage

- Bytes are stored under a separate directory, `data/files/<uuid>`, apart from the legacy
  API's `data/uploads/`. An environment variable such as `FILE_LIBRARY_DIR` can override
  it. Docker needs one more volume.
- The original filename is metadata only and never becomes part of a path.
- Write order:
  1. Write to a temporary file while computing `sha256` and size.
  2. Validate the file type.
  3. Rename the temporary file to its final location.
  4. Insert the record into the `files` table.
  5. If step 4 fails, remove the file just written.
- No new migration is needed; the `files` table already has every column.
- Two identical files are stored separately, because the schema allows duplicate `sha256`.

## 8. User interface

Add a new page `/files` and a "File library" item in the navigation bar. Existing pages do not change.

| Element | Behavior |
|---|---|
| Upload button | Select one or more files. Check extension and size in the browser first |
| File table | Name, type, size, upload date. Newest first |
| Download button | Fetch the file through the client that attaches the token, then save it from a blob. A plain `<a href>` cannot send the token |
| Delete button | Ask for confirmation in an in-app dialog (not `window.confirm`), then delete and refresh the list |
| Error messages | Each failed action shows a clear message: too large, wrong type, no connection, session expired |
| Empty state | Show a prompt to upload the first file |

Browser checks only report errors early. The server still validates everything.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Download holds the whole file (up to 100 MiB) in browser memory | Accept at the current scale. If needed, switch to short-lived signed download links |
| Soft-deleted files fill the disk over time | Monitor disk usage. Define a cleanup rule once citations and inquiries exist |
| Disk and database drift: bytes without a record | Use the write order in section 7. Orphan bytes are harmless and can be removed by a later script |
| Users confuse the file library with the upload tab on the Capture page | State on the page that library files are not added to any knowledge base |

## 10. Success criteria

- Uploads of the 5 types succeed. Wrong extension, mismatched content, files over 100 MiB
  and empty files are rejected with the codes in section 4.
- User A cannot view, download or delete user B's files. Those requests return `404`.
- After deletion, the file is gone from the list, but its bytes and record remain with `deleted_at` set.
- Any endpoint called without a token returns `401`.
- The `/files` page supports upload, download and delete. Each failed action shows a message.
- The Capture and Knowledge management pages work as before.

## 11. Proposed phases

1. **Backend storage and type validation:** type checker, byte storage, `files` table queries. Unit tests first.
2. **Backend API:** the 5 endpoints, sign-in checks, the `can_read` function. Integration tests with PostgreSQL.
3. **Frontend:** API service, `/files` page, error messages. Unit tests and Playwright end-to-end tests.
4. **Documentation and final checks:** add `docs/features/file-library.md`, update the README, run all tests.
