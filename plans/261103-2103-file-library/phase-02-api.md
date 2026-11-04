# Phase 2: Service and API

## Context links

- [Plan overview](plan.md), [phase 1](phase-01-storage-and-types.md)
- Sign-in dependency: `require_current_user` in `backend/api/auth.py`
- App wiring: `create_app` in `backend/app.py`
- Postgres test setup to reuse: `PostgresAuthTest` in `backend/tests/test_auth.py`
- Thread pool pattern for disk work: `backend/api/document_uploads.py`

## Overview

- Priority: high.
- Status: complete.
- Adds the service with business rules, the 5 HTTP endpoints and the app wiring.

## Key insights

- `require_current_user` already returns the caller's account, so each route only adds the owner filter.
- In tests, `create_app` takes injected services and never opens the database. The file service must be injectable the same way.
- The database engine is currently created only when auth is not injected. Create it once whenever `APP_DATABASE_URL` is set, and share it.
- Starlette's `FileResponse` already writes a correct `Content-Disposition` for non-ASCII names, such as Vietnamese filenames.

## Requirements

Functional (contract):

| Method and path | Success | Errors |
|---|---|---|
| `POST /v1/files` (multipart field `file`) | `201` metadata | `401`, `413`, `415`, `422`, `503` |
| `GET /v1/files` | `200` `{"files": [...]}`, newest first | `401`, `503` |
| `GET /v1/files/{id}` | `200` metadata | `401`, `404`, `503` |
| `GET /v1/files/{id}/content` | `200` bytes | `401`, `404`, `503` |
| `DELETE /v1/files/{id}` | `204` | `401`, `404`, `503` |

- Metadata: `id`, `filename`, `content_type`, `size_bytes`, `sha256`, `created_at`. Never `storage_key`.
- Filename: 1–255 characters, no control characters; otherwise `422`.
- `404` for a missing, deleted or another user's file. The same code for all three.
- Download headers: `Content-Disposition: attachment; filename*=...` and `X-Content-Type-Options: nosniff`.

Non-functional:

- Disk work runs in a thread pool so it does not block other requests.
- Database failures return `503` without connection details.

## Architecture

```
HTTP route (backend/api/files.py)
  └─ require_current_user  -> Account
  └─ FileLibraryService    (backend/src/services/file_library.py)
       ├─ FileBlobStore    (phase 1)
       └─ FileRecordStore  (phase 1)
```

Service sketch:

```python
class FileLibraryService:
    """Upload, list, read and delete files owned by one user."""

    async def upload(self, user: Account, filename: str, stream: BinaryIO) -> FileMetadata: ...
    async def list(self, user: Account) -> list[FileMetadata]: ...
    async def get(self, user: Account, file_id: UUID) -> FileMetadata: ...
    async def open_content(self, user: Account, file_id: UUID) -> tuple[FileMetadata, Path]: ...
    async def delete(self, user: Account, file_id: UUID) -> None: ...

    def can_read(self, record: FileRecord, user: Account) -> bool:
        """Decide whether a user may read a file.

        Now: only the owner, and only while the file is not deleted.
        Later: readers of a citation or inquiry that references the file, including deleted files.
        """
```

Upload order:

1. Validate the filename.
2. `save_temp` → `detect_file_type` → `publish` → `insert`.
3. If `insert` fails, remove the published bytes and re-raise.

Early size check: before parsing the body, the route rejects a `Content-Length` above
100 MiB + 1 MiB (multipart overhead) with `413`. The limit during copying stays the real check.

## Related code files

| Action | Path |
|---|---|
| Create | `backend/src/services/file_library.py` |
| Create | `backend/api/files.py` |
| Modify | `backend/app.py` (new `file_library` argument, shared engine, router, `503` handler) |
| Modify | `backend/tests/test_file_library.py` (API and database tests) |

## Implementation steps

1. Write API tests first, using a disposable migrated database as in `PostgresAuthTest`. Sign up two users through `/auth`.
2. Cases to cover:
   - Upload each type → `201`; list shows it; metadata has no `storage_key`.
   - Renamed `.exe` → `415`; empty → `422`; oversized → `413`; no token → `401`.
   - User B gets `404` on user A's file for get, content and delete.
   - After delete: list no longer shows it, and get, content and a second delete return `404`. The row has `deleted_at`. The bytes are still on disk.
   - Download of a Vietnamese filename returns the correct `Content-Disposition`.
   - A database failure during insert leaves no bytes on disk (mock `insert` to raise).
   - No service configured → `503`.
3. Implement `FileLibraryService`.
4. Implement `backend/api/files.py`.
5. Wire into `backend/app.py`.
6. Run the auth and legacy upload tests to confirm nothing changed.

## Todo list

- [x] API tests written and failing
- [x] `FileLibraryService` with `can_read`
- [x] `backend/api/files.py` routes
- [x] `create_app` wiring and `503` handler
- [x] New tests pass with `TEST_DATABASE_URL` set
- [x] `backend.tests.test_auth` and `backend.tests.test_document_uploads` still pass (regression run with app database tests: 37/37)

## Success criteria

- Every row of the contract table has a passing test.
- No test reads or writes `data/uploads/` (the legacy storage).

## Risk assessment

| Risk | Mitigation |
|---|---|
| Multipart parsing receives the whole body before the route runs | Early `Content-Length` check; the copy-time limit stays the source of truth |
| Shared engine change breaks auth startup | Keep the auth tests in the regression run |
| Bytes exist without a row after a crash between publish and insert | Harmless; a cleanup script can be added later |

## Security considerations

- Every query filters by owner. Ownership is never taken from the request body.
- One `404` for "not yours" and "does not exist", so file IDs cannot be probed.
- `nosniff` and `attachment` stop the browser from running downloaded content.

## Next steps

- Phase 3 calls these endpoints.
- When citations and inquiries ship, add their reader rule to `can_read` and test reading a deleted file through them.

## Deviations (as built)

- Read path: `FileRecordStore.get(file_id)` returns any record. `can_read` alone decides access. Approved by the user.
- Upload is refused with `411` when `Content-Length` is missing or not numeric, or when any `Transfer-Encoding` header is present. The plan only covered the `413` pre-check. Chunked bodies could otherwise bypass the size limit.
- Multipart errors (two files, more than 10 fields, malformed body) return `400`.
- Save, type check and publish run in one worker thread. If the insert is cancelled, the published bytes are not removed.
- Tests: offline service and API tests use a stub auth service and an in-memory record store, in addition to the PostgreSQL tests.
