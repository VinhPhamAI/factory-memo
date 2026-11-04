# Phase 4: Deployment, docs and checks

## Context links

- [Plan overview](plan.md), phases [1](phase-01-storage-and-types.md), [2](phase-02-api.md), [3](phase-03-frontend.md)
- Compose file: `deployments/docker-compose.yml` (has the `document_uploads` volume as an example)
- Environment example: `.env.example` (has `DOCUMENT_UPLOAD_DIR` as an example)

## Overview

- Priority: medium.
- Status: complete, with open checks (see results below).
- Makes the feature run in Docker, documents it, and runs every check.

## Key insights

- Only the API container reads and writes library files. The worker does not.
- Without a named volume, Docker would lose files when the container is recreated.

## Requirements

- A Docker named volume `file_library` mounted at `/app/data/files` on the `api` service.
- `FILE_LIBRARY_DIR` documented in `.env.example`.
- One feature guide that a new reader can follow without chat history.

## Architecture

| Runtime | Storage root |
|---|---|
| Host run (`uv run uvicorn ...`) | `<project>/data/files/`, or `FILE_LIBRARY_DIR` |
| Docker Compose API | `/app/data/files/`, on the named volume `file_library` |

## Related code files

| Action | Path |
|---|---|
| Modify | `deployments/docker-compose.yml` |
| Modify | `.env.example` |
| Create | `docs/features/file-library.md` |
| Modify | `README.md`, `docs/README.md` (link to the guide) |
| Modify | `CLAUDE.md` and `AGENTS.md` ("Read when relevant" line; keep both identical) |

`data/files/` needs no `.gitignore` change: `.gitignore` already ignores `data/`.

## Implementation steps

1. Add the volume and the environment variable to the `api` service.
2. Add `FILE_LIBRARY_DIR=` with a one-line comment to `.env.example`.
3. Write `docs/features/file-library.md`:
   - Scope and what is not implemented (preview, knowledge base ingestion).
   - Endpoint table, limits, accepted types and how they are checked.
   - Deletion rule: soft delete, bytes kept, reason.
   - Storage location per runtime.
4. Add links from `README.md`, `docs/README.md`, `CLAUDE.md` and `AGENTS.md`.
5. Run the checks below and record the results in `plans/261103-2103-file-library/reports/`.

## Todo list

- [x] Compose volume and variable
- [x] `.env.example` entry
- [x] `docs/features/file-library.md`
- [x] Links added; `cmp AGENTS.md CLAUDE.md` is clean
- [ ] All checks run and results recorded (check 6 open, see results below)

## Checks

1. `uv run python -m unittest backend.tests.test_file_types backend.tests.test_file_library -v`
   (PostgreSQL cases need `TEST_DATABASE_URL`; report them as skipped if it is unset).
2. `uv run python -m unittest backend.tests.test_auth backend.tests.test_document_uploads -v`.
3. From `frontend/`: `npm run build`, `npm run lint`,
   `npm run test -- src/services/file-api.test.ts src/pages/files.test.tsx`,
   `npm run test:e2e -- e2e/files.spec.ts`.
4. Manual, in live mode with two users:
   - Each user sees only their own files.
   - Each of the 5 types uploads; a renamed `.exe` is rejected.
   - A Vietnamese-named file downloads with its original name.
   - A deleted file disappears from the list.
5. The Capture page upload and the Knowledge management page still work.
6. `make setup` reruns without losing files on the `file_library` volume.
7. `git diff --check` and `cmp AGENTS.md CLAUDE.md`.

## Results

| Check | Result |
|---|---|
| 1 | Pass: backend file library tests 37/37, PostgreSQL run via `TEST_DATABASE_URL` |
| 2 | Pass: auth, document upload and app database regression 37/37 |
| 3 | Pass: `npm run build`, `npm run lint`; file tests 16/16; `PLAYWRIGHT_SUITE=files` 1/1; existing `qa.spec.ts` 6/6 |
| 4 | Covered by automated tests, not by hand: `PLAYWRIGHT_SUITE=files` (two users, own files only, rejected renamed file, Vietnamese filename download, delete) and the PostgreSQL API tests (each of the 5 types; renamed `.exe` → `415`) |
| 5 | Covered by automated tests, not by hand: `test_document_uploads` (legacy upload API), frontend `capture.test.tsx` and `knowledge-management.test.tsx` in the full Vitest run, `qa.spec.ts` 6/6 |
| 6 | Not run: `make setup` rebuilds and restarts the user's Docker stack. Compose volume is in place, and the container runs as root, so the volume is writable |
| 7 | Pass: `git diff --check` clean except the `.gitignore` whitespace that predates this work; `cmp AGENTS.md CLAUDE.md` identical |

Pre-existing failures, reproduced on a clean HEAD, not caused by this work:

- `test_docker_setup`: repo path with a space breaks the fake `docker` shebang.
- Missing knowledge base packages: `enterprise_rag`, `assetopsbench`, `streamlit_app`.
- Stale approval assertions in knowledge tests.
- Missing `data/case-mr-je-ax167.json` for two frontend suites.

## Deviations (as built)

- `make setup` was not rerun to test volume survival. The rerun restarts the user's Docker stack, so it needs the user's go-ahead.
- Playwright run uses `PLAYWRIGHT_SUITE=files`. See phase 3.

## Success criteria

- Every check passes, or a skipped check is reported with its reason.
- The guide states what is implemented and what is not.

## Risk assessment

| Risk | Mitigation |
|---|---|
| `docker compose down -v` deletes the volume | State this in the guide |
| Soft-deleted files fill the disk | State this in the guide; a cleanup rule comes with citations and inquiries |

## Security considerations

- No credentials in `.env.example` or the guide.

## Next steps

- Update `plan.md` status to `complete`.
- When citations and inquiries ship, extend `can_read` and retest reading deleted files.
