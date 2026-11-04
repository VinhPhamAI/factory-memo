# docs-manager report: file library general docs

Date: 2026-11-04. Scope: `docs/development/codebase-summary.md`, `docs/architecture/system-architecture.md`, `docs/architecture/database-schema.md`.

## Changes

| File | Change | Reason |
|---|---|---|
| `docs/development/codebase-summary.md` | Added a row for the file library code locations (`backend/api/files.py`, `backend/src/services/file_*.py`, `backend/src/storage/file_library.py`). | New code location for readers. |
| `docs/development/codebase-summary.md` | Replaced "services do not use them yet" with "only the authentication and file library code use them so far." | The statement was outdated; `files` is now written by the file library. |
| `docs/architecture/system-architecture.md` | Added a "Personal file library" row to "Current implementation boundaries": implemented routes, storage locations, and what is not implemented. | Current status belongs in this table. |
| `docs/architecture/database-schema.md` | Status line now lists `users`, `auth_sessions`, and `files` as used. States that the file library API writes `files` and no migration was needed. | Previously said only `users` and `auth_sessions` were used. |
| `docs/architecture/database-schema.md` | Under the `files` table, added a "Current implementation" note: the only read rule is owner access to a non-deleted file (`FileLibraryService.can_read`); source/inquiry access is not implemented. Linked to `docs/features/file-library.md`. | The table's target permission rule would otherwise be read as current behavior. |

## Verified against code

- `backend/src/storage/orm/files.py` columns match the `files` table in the schema doc (`filename`, `content_type`, `size_bytes`, `storage_key` unique, `sha256`, `uploaded_by` to `users.id`, record dates with `deleted_at`).
- Route prefix `/v1/files` and wiring in `backend/app.py` match the feature doc.
- `FILE_LIBRARY_DIR` appears in `.env.example` and `deployments/docker-compose.yml`, consistent with the storage notes.
- All edited files pass `git diff --check`.

## Not changed

- `docs/features/file-library.md`, `README.md`, `docs/README.md`, `frontend/README.md`, `CLAUDE.md`, `AGENTS.md`, `plans/`, and code: out of scope.
- `docs/development/database-migrations.md` line 6-7 says the API uses only `users` and `auth_sessions`. It is outside this task's file list and is now outdated. Update it in a separate pass.
- `docs/architecture/database-schema.md` "Files and machines" and "Upload metadata" rows in the migration table still describe the target import. These remain accurate as target-state text.
- `docs/architecture/system-architecture.md` backend responsibilities row was not changed. It already covers "application records".

## Items for the main session

1. `voice-agent/CLAUDE.md` and `voice-agent/AGENTS.md` say "services do not use them yet" about application tables. This is partly outdated: the file library writes `files`. Not edited, per instructions. `AGENTS.md` must stay identical to `CLAUDE.md`.
2. `docs/development/database-migrations.md` (see above).
3. The doc validator `.claude/scripts/validate-docs.cjs` was not found under the repository root or `voice-agent/`, so it was not run.

## Metrics

- Files edited: 3 of 3 allowed. Line counts: codebase-summary 49, system-architecture 71, database-schema 478 (all under the 800 LOC limit).
