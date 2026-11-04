---
title: Personal file library
status: complete
created: 2026-11-03
base_commit: ee1c465  # rebased from a5ef5f5 on 2026-11-04
---

# Personal file library

Signed-in users get a private library: upload, list, view details, download and delete
their own files. Decisions and rejected options: [brainstorm summary](brainstorm-summary.md).

## Scope

| In scope | Out of scope |
|---|---|
| PDF, XLSX, DOCX, JPEG, PNG; max 100 MiB per file | In-browser preview |
| Owner-only access | Admin access to other users' files |
| Soft delete; bytes kept for future citation/inquiry readers | Citations and inquiries themselves |
| New API `/v1/files` and new page `/files` | Changes to the legacy `/v1/documents/{kb}/uploads` API |

No database migration is needed. The `files` table already exists with every column.

## API contract

All endpoints require `Authorization: Bearer <access_token>`.

| Method and path | Success | Errors |
|---|---|---|
| `POST /v1/files` (multipart field `file`) | `201` metadata | `401`, `413`, `415`, `422`, `503` |
| `GET /v1/files` | `200` `{"files": [...]}`, newest first | `401`, `503` |
| `GET /v1/files/{id}` | `200` metadata | `401`, `404`, `503` |
| `GET /v1/files/{id}/content` | `200` bytes as attachment | `401`, `404`, `503` |
| `DELETE /v1/files/{id}` | `204` | `401`, `404`, `503` |

- Metadata fields: `id`, `filename`, `content_type`, `size_bytes`, `sha256`, `created_at`.
- `404` covers a missing file, a deleted file and another user's file.

## Phases

| # | Phase | Depends on | Status |
|---|---|---|---|
| 1 | [Storage and file type check](phase-01-storage-and-types.md) | none | complete |
| 2 | [Service and API](phase-02-api.md) | 1 | complete (deviations noted) |
| 3 | [Frontend](phase-03-frontend.md) | 2 (contract only; can start with mocks) | complete (deviations noted) |
| 4 | [Deployment, docs and checks](phase-04-docs-and-checks.md) | 1–3 | complete, with open checks |

Build each phase test-first. Follow [engineering standards](../../docs/development/code-standards.md).

Status: code, tests and docs are done on branch `feat/file-library`. Changes are not committed.

## Open items

Finish these before merge. The plan is not fully verified until they are done.

- Phase 4 check 6 (`make setup` keeps the `file_library` volume) is not run: it rebuilds and restarts the Docker stack, so it needs the user's go-ahead.
- Phase 4 checks 4 and 5 are covered by automated tests only, not by a manual run.
- Follow-ups, non-blocking: S4 (Refresh enabled during upload can hide a new file), S6 (no screen-reader text during upload), and extending `can_read` for citations and inquiries.

## Done when

- The 5 types upload; wrong type, mismatched content, oversized and empty files are rejected.
- User A cannot view, download or delete user B's files.
- Deleted files leave the list, but bytes and the record (with `deleted_at`) remain.
- The `/files` page uploads, downloads and deletes, and shows an error for each failed action.
- Capture and Knowledge management pages behave as before.

## Follow-up

When citations and inquiries ship, extend `can_read` (phase 2) and retest reading deleted files.
