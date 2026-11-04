# Tester report: personal file library (branch feat/file-library, uncommitted)

No source, test, config or doc files edited. `git status --short` identical before and after. No `git stash` used; clean-HEAD checks ran in temporary worktrees, now removed.

## Test commands

| # | Command | Run | Pass | Fail | Error | Skip | Result |
|---|---|---|---|---|---|---|---|
| 1 | `unittest backend.tests.test_file_types backend.tests.test_file_library` | 26 | 26 | 0 | 0 | 0 | OK |
| 2 | `unittest test_auth test_document_uploads test_app_database test_docker_setup` | 47 | 36 | 0 | 14 | 0 | FAILED (all 14 in test_docker_setup) |
| 3 | `unittest discover -s backend/tests -t .` (broad) | 554 | 524* | 8 | 19 | 3 | FAILED |
| 4a | `frontend: npm run build` | - | - | - | - | - | exit 0, no warnings in output |
| 4b | `frontend: npm run lint` | - | - | - | - | - | exit 0, no output |
| 4c | `frontend: npx vitest run` | 85 tests | 85 | 0 | 2 suites | - | 2 failed / 12 passed suites |
| 4d | `vitest files.test.tsx file-api.test.ts` | 15 | 15 | 0 | 0 | - | OK |

\* 524 derived as 554 minus failures, errors and skips. Subtests make the unittest count inexact.

Playwright not run (per task).

## Failures and classification

### Regression: none found

### Broad suite (backend), all reproduced identically on clean HEAD

Clean HEAD = `git worktree add ... HEAD`, run on the failing modules only.

| Failure | Classification | Evidence |
|---|---|---|
| 14 errors, `test_docker_setup` (10 methods; 7 subtests of `test_infrastructure_errors_stop_the_remaining_steps`) | Pre-existing, environment | Fake `docker` script uses shebang `#!{sys.executable}`. Working tree interpreter is `/Users/uncpham/Hackathon/Denso 2026/voice-agent/.venv/bin/python`; the space breaks the shebang: `bad interpreter` (RC 126). Clean HEAD at a path without spaces: 10/10 OK. Clean HEAD at a path with spaces (`.../spaced parent/clean head`): same 14 errors. Diff touches only `.env.example` and `docker-compose.yml`, which the test does not read. |
| 3 import errors: `test_assetopsbench`, `test_enterprise_rag`, `test_streamlit_app` | Pre-existing, missing packages | `No module named 'backend.knowledge_bases.enterprise_rag'`, `No module named 'streamlit_app'`. Same on clean HEAD. CLAUDE.md lists missing KB packages. |
| 1 error `test_enterprise_rag_filters_on_source_type` | Pre-existing, missing package | Same as above. |
| 1 error `test_support_links_resolve_within_each_conversation` (knowledge_ingestion_service) | Pre-existing | Also errors on clean HEAD. |
| 4 failures: `test_knowledge_store` x2, `test_knowledge_ingestion_service` x3 (`test_ingest_links_records_to_evidence`, `test_records_default_to_approved_without_links`, `test_results_align_with_inputs_and_failures_are_isolated`) | Pre-existing, stale approval assertions | Records default to approved in tests; CLAUDE.md says draft lifecycle is the rule. Same failures on clean HEAD. |
| `test_voice_capture` `test_disconnect_without_stop_keeps_audio_unlinked` | Pre-existing | Same on clean HEAD. |
| `test_industrial_qa_pipeline` x2 (`test_packaged_config_matches_code_defaults`, `test_toml_llm_settings_override_environment`) | Pre-existing, config drift | Expected 8192, got 20000 (EXTRACTION setting). Same on clean HEAD. |

Clean-HEAD run of the failing modules: 133 tests, 8 failures, 5 errors. Set of failing test names matches the working tree exactly, except the docker module (passes on clean HEAD at a no-space path).

Caveat: clean worktree lacks gitignored local files. Outcomes still matched.

### Frontend vitest

- `src/fixtures/ax167.test.ts` and `src/services/case-card.test.ts`: `ENOENT ... data/case-mr-je-ax167.json`. Only cause confirmed; both suites fail at load with no other error.

### New file-library tests

All 26 backend tests and both frontend suites (`files.test.tsx`, `file-api.test.ts`) pass.

## Performance

- Backend file-library tests: 3.7 s wall.
- Broad suite: 23.8 s.
- Frontend build: 581 ms. Vitest: 1.5 s for the new suites.

## Untested edge cases (not written, per task)

| Severity | Location | Case |
|---|---|---|
| High | `backend/api/files.py:91-94` | Content-Length pre-check only runs when the header is present. Chunked upload skips it. Starlette spools file parts to disk before `save_temp` enforces 100 MiB. `max_part_size` (1 MiB) applies to non-file fields only (Starlette 1.10 signature). Authenticated user can fill temp disk. Needs test: chunked body over 100 MiB. Not verified by running. |
| High | `backend/src/services/file_library.py:96-102` and `:112-114` | `asyncio.to_thread` keeps running after the awaiting task is cancelled (client disconnect). Thread can finish `save_temp` or `publish` after the `except` cleanup ran, leaving a `.upload-*` temp file or published bytes with no record. No sweeper exists. Needs test: cancel mid-upload, check the root. |
| Medium | `backend/src/services/file_library.py:103-114` plus `backend/src/storage/file_library.py:209-211` | Commit succeeds, then an error or cancel after commit runs `blobs.remove`. Record now points to missing bytes. Download returns 404 with no log. Needs test: record without bytes. |
| Medium | `backend/src/storage/file_library.py:223-234` | `list_owned` has no limit or pagination. Response grows with the library. |
| Medium | `backend/api/files.py:137-143` | `FileResponse` raises after the `is_file` check in `open_content` if the file is removed between check and open. Results in 500. Narrow window. |
| Medium | `backend/src/services/file_types.py:68-75` | DOCX/XLSX check only looks for one member name. An `.xlsx` containing `word/document.xml` and `xl/workbook.xml` passes. No cross-type check. |
| Low | `frontend/src/services/file-api.ts:127-139` | Whole download held in memory (up to 100 MiB). Object URL revoked on `setTimeout(0)`; some browsers may cancel the download. Not confirmed in Playwright (only 1 files e2e test ran). |
| Low | `backend/src/services/file_library.py:47-58` | Filename validation: no test for path separators (`a/b.pdf`), bare `.pdf` (no stem, rejected as unsupported), or 255-char boundary in Unicode. |
| Low | `backend/src/storage/file_library.py:113` | Storage key from `uuid4` with no collision check. Negligible. |

## Verdict

**Pass with caveats.** New file-library code: 26/26 backend tests, 15/15 new frontend tests, build and lint clean. No regressions. The 14 docker-setup errors and all broad-suite failures are pre-existing: the docker one is a repo-path-with-spaces harness issue; the rest match CLAUDE.md's known causes or config drift.

Before merge, consider the two High edge cases (chunked oversize upload spooling to disk; cancelled upload leaking temp or published bytes). Both lack tests.

## Unresolved questions

1. Starlette multipart: confirm file parts have no size limit at parse time (read from signature only, not from test).
2. Playwright download behavior with `setTimeout(0)` revoke: not checked; the files e2e suite may not cover download.
3. Docker-setup test: fix path (use a path-independent shebang or `#!/usr/bin/env python3`) is a test-harness change; not made, per task.
