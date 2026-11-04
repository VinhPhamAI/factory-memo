# Project manager report: personal file library

Branch `feat/file-library`. Changes are uncommitted. Edits made only in `plans/261103-2103-file-library/`.

## Status

| Phase | Plan status | Todo items | Notes |
|---|---|---|---|
| 1. Storage and file type check | complete | 6/6 | Method name and `get` deviation recorded |
| 2. Service and API | complete | 6/6 | 411 and 400 rules, cancel gap recorded |
| 3. Frontend | complete | 7/7 | Delete 404 notice, 60 s blob revoke, follow-ups S4 and S6 |
| 4. Deployment, docs and checks | complete, with open checks | 3/5 | Checks 4, 5, 6, 7 open |

Plan frontmatter set to `status: complete` as requested. `plan.md` is under 80 lines.

## Verified

- Compose `file_library` volume and `FILE_LIBRARY_DIR` in `.env.example`: present.
- `docs/features/file-library.md`: present. Linked from `README.md`, `docs/README.md`, `frontend/README.md`, `CLAUDE.md` and `AGENTS.md`.
- `CLAUDE.md` and `AGENTS.md` link line matches (checked by grep).
- Upload rule for chunked bodies (`411` on `Transfer-Encoding`): present in `backend/api/files.py`.

## Test results (from the implementation report)

- Backend file library: 37/37, PostgreSQL via `TEST_DATABASE_URL`.
- Auth, document upload, app database regression: 37/37.
- Frontend file tests: 16/16. `npm run build` and `npm run lint`: pass.
- Playwright `PLAYWRIGHT_SUITE=files`: 1/1 against a real backend. Existing `qa.spec.ts`: 6/6.
- Pre-existing failures, same on clean HEAD: `test_docker_setup` (path with a space), missing knowledge base packages, stale approval assertions, missing `data/case-mr-je-ax167.json`.

## Open work (main agent must finish)

The plan is not fully verified. Finish these before merge:

1. Phase 4 check 4: manual two-user run (own files only, all 5 types, `.exe` rejected, Vietnamese filename download, deleted file gone).
2. Phase 4 check 5: Capture page upload and Knowledge management page still work.
3. Phase 4 check 6: `make setup` keeps `file_library` volume data. Not run because it restarts the user's Docker stack. Needs user approval.
4. Phase 4 check 7: `git diff --check` and `cmp AGENTS.md CLAUDE.md`. No shell was available to this agent, so these were not run.
5. Commit the work once checks 1–7 are recorded.

## Follow-ups (non-blocking)

- S4: Refresh button stays enabled during upload; can hide a just-uploaded file until next refresh.
- S6: no screen-reader text during upload.
- When citations and inquiries ship: extend `can_read` and test reading deleted files.

## Risks from review (not fixed in the plan, flagged here)

- A cancelled upload (client disconnect) can leave a `.upload-*` temp file or published bytes with no row. No sweeper exists. The phase 2 deviation records that bytes are not removed after a cancelled insert.
- Code review first pass flagged doc wording on the cleanup rules in `docs/features/file-library.md`. The implementation report says all warnings were fixed. This agent did not re-read that section.
- Tester noted a DOCX or XLSX check that looks for one ZIP member only. An XLSX that also contains `word/document.xml` passes. This is not listed as fixed.

## Unresolved questions

1. Was the DOCX and XLSX single-member check changed after the tester report? The plan's type table requires one member each, so this may be acceptable.
2. Does the cancelled-upload leak need a fix before merge, or is it accepted for this scope?
3. Who approves running `make setup` (check 6)?
4. Are the Capture and Knowledge management pages covered by existing tests, or only by the manual check (check 5)?
