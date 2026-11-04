# Phase 1: Storage and file type check

## Context links

- [Plan overview](plan.md), [brainstorm summary](brainstorm-summary.md)
- `files` table model: `backend/src/storage/orm/files.py`
- Copy pattern to reuse: `DocumentUploadStore.save` in `backend/src/storage/document_uploads.py`
- Database error pattern to reuse: `AuthStore._transaction` in `backend/src/storage/auth.py`

## Overview

- Priority: high. Phases 2–4 build on it.
- Status: complete.
- Builds two independent pieces: a file type checker and a storage layer (disk + database).

## Key insights

- The `files` table exists and has `uploaded_by` and `deleted_at`. No migration is needed.
- The browser's MIME type can be anything, so the server decides the type from the extension and the first bytes.
- DOCX and XLSX are ZIP archives. Checking one known file inside the ZIP separates them from a plain ZIP.
- Only the Python standard library is needed (`zipfile`, `hashlib`).

## Requirements

Functional:

- Accept a file only when the extension and the content match one row:

  | Extension | Content type | First bytes | Required ZIP member |
  |---|---|---|---|
  | `.pdf` | `application/pdf` | `%PDF-` | |
  | `.jpg`, `.jpeg` | `image/jpeg` | `FF D8 FF` | |
  | `.png` | `image/png` | `89 50 4E 47 0D 0A 1A 0A` | |
  | `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `PK\x03\x04` | `word/document.xml` |
  | `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | `PK\x03\x04` | `xl/workbook.xml` |

- Compare extensions case-insensitively (`.PDF` is valid).
- Reject empty files and files over 100 MiB. Stop reading as soon as the limit is passed.
- Store bytes at `<root>/<uuid hex>`. The original filename never becomes a path.

Non-functional:

- No partial or temporary file remains after any error.
- Database connection errors raise one dedicated exception, so the API can answer `503`.

## Architecture

```
upload stream ──> FileBlobStore.save_temp ──> detect_file_type ──> FileBlobStore.publish ──> FileRecordStore.insert
                  (size, SHA-256, limit)      (extension + bytes)    (rename to <uuid>)        (row in `files`)
```

Sketch of the type checker:

```python
class UnsupportedFileType(ValueError):
    """The file is not one of the accepted types, or its content does not match its extension."""

ALLOWED = {
    ".pdf":  ("application/pdf", b"%PDF-", None),
    ".png":  ("image/png", b"\x89PNG\r\n\x1a\n", None),
    ".jpg":  ("image/jpeg", b"\xff\xd8\xff", None),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff", None),
    ".docx": (DOCX_MIME, b"PK\x03\x04", "word/document.xml"),
    ".xlsx": (XLSX_MIME, b"PK\x03\x04", "xl/workbook.xml"),
}

def detect_file_type(filename: str, path: Path) -> str:
    """Return the content type chosen by the server for a stored upload.

    Raises:
        UnsupportedFileType: unknown extension, wrong first bytes, or missing ZIP member.
    """
```

Storage classes:

| Class | Methods | Notes |
|---|---|---|
| `FileBlobStore(root)` | `save_temp(stream, max_bytes) -> TempUpload`, `publish(temp, key)`, `path(key)`, `remove(key)` | `TempUpload` holds temp path, size and SHA-256. Root from `FILE_LIBRARY_DIR`, default `<project>/data/files` |
| `FileRecordStore(sessions)` | `insert(...)`, `list_owned(user_id)`, `get_owned(file_id, user_id)`, `soft_delete(file_id, user_id) -> bool` | Every read filters `uploaded_by = user_id AND deleted_at IS NULL` |

Soft delete is one statement:

```sql
UPDATE files SET deleted_at = now()
WHERE id = :id AND uploaded_by = :user AND deleted_at IS NULL
```

Zero updated rows means "not found".

## Related code files

| Action | Path |
|---|---|
| Create | `backend/src/services/file_types.py` |
| Create | `backend/src/storage/file_library.py` |
| Create | `backend/tests/test_file_types.py` |
| Create | `backend/tests/test_file_library.py` (storage part) |
| Read only | `backend/src/storage/orm/files.py`, `backend/src/storage/document_uploads.py`, `backend/src/storage/auth.py` |

## Implementation steps

1. Write `test_file_types.py`: one valid sample per type, built in the test (for example a tiny ZIP with `word/document.xml`).
2. Add failing cases: `.exe` bytes named `.pdf`, plain ZIP named `.docx`, XLSX named `.docx`, no extension, `.txt`.
3. Implement `file_types.py` until the tests pass.
4. Write blob store tests: normal save, empty file, file one byte over the limit, temp file removed after each error.
5. Implement `FileBlobStore`, reusing the 1 MiB chunk loop from `DocumentUploadStore.save`.
6. Implement `FileRecordStore` with the `_transaction` pattern; map connection errors to `FileStorageUnavailable`.
7. Database tests for `FileRecordStore` run in phase 2 together with the API tests (they need PostgreSQL).

## Todo list

- [x] Type checker tests written and failing
- [x] `file_types.py` implemented
- [x] Blob store tests written and failing
- [x] `FileBlobStore` implemented
- [x] `FileRecordStore` implemented
- [x] `uv run python -m unittest backend.tests.test_file_types backend.tests.test_file_library -v` passes (backend file library tests: 37/37 with `TEST_DATABASE_URL`)

## Success criteria

- All type checker cases pass, including the rejected ones.
- No file remains in the storage root after a failed save.

## Risk assessment

| Risk | Mitigation |
|---|---|
| A malformed ZIP makes `zipfile` raise an unexpected error | Catch `zipfile.BadZipFile` and treat it as `UnsupportedFileType` |
| Rename across disks fails | Create the temp file inside the storage root, so rename stays on one disk |

## Security considerations

- Never build paths from the client filename.
- Never trust the client MIME type.
- `storage_key` is internal and is never returned to clients.

## Next steps

- Phase 2 uses these classes in the service and API.

## Deviations (as built)

- `FileRecordStore` method names differ from the plan. There is `get(file_id)` instead of `get_owned`.
- `get(file_id)` returns any record, including deleted files and other users' files.
  Read permission is decided only by `FileLibraryService.can_read` (phase 2). Approved by the user.
- `list_owned` and `soft_delete` filter by owner in SQL, as planned.
