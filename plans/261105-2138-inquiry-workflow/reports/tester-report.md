# Tester report: inquiry workflow (2026-11-05)

Scope: working tree on main@3f5c7de, uncommitted. Nothing committed.

| Suite | Result |
|---|---|
| Backend `unittest discover` | 720 run, 15 F, 23 E, 33 skip. ~649 pass (derived). All failures pre-existing |
| Inquiry modules | test_inquiries 25, test_inquiry_draft_route 7, test_inquiry_smoke 1, test_conversations 12, test_file_library 33, test_application_startup 3, test_library_knowledge_uploads 6: all pass |
| Agent `pytest tests/unit` | 113 passed |
| Frontend `build` / `lint` | pass / pass |
| Frontend `test` | 154 passed; 2 suites fail to load (known: missing `data/case-mr-je-ax167.json`) |
| Playwright `PLAYWRIGHT_SUITE=inquiries` | 1 passed (9.3s) |
| `git diff --check` / `cmp AGENTS.md CLAUDE.md` | only `.gitignore:188` (known) / identical |

## Regressions

None. The failing set in every touched or dependent module (`test_knowledge_conflicts`, `test_industrial_qa_pipeline`, `test_knowledge_ingestion_service`, `test_knowledge_store`, `test_docker_setup`, `test_source_converters`, `test_knowledge_bases`) is identical to a clean HEAD worktree run: 35 entries, same names.

Pre-existing causes:
- Missing KB packages: `enterprise_rag`, `assetopsbench`, `streamlit_app` (import errors).
- `test_docker_setup` (14 errors): fake-docker fixture breaks on the temp path with a space.
- `test_knowledge_store` and `test_knowledge_ingestion_service` (5): stale draft/approved assertions and an old kwarg.
- `test_source_converters` (3): missing `backend/tests/fixtures/formats/manual.pdf`.
- `test_knowledge_conflicts` (7): 503 "Authentication is not configured" on the injected-services path. Unchanged at HEAD.
- `test_industrial_qa_pipeline` (4): TOML/config and HTTP 401 cases.

## Playwright

- Path: conversation and draft created via API (`POST /v1/conversations`, `POST /v1/inquiries`). Agent offline, so the draft-from-chat button path is not run here; `test_inquiry_draft_route` covers it.
- Flow passed: employee uploads, attaches, submits; expert asks ("Hỏi thêm"); employee replies ("Trả lời"); expert accepts ("Chấp nhận"). Timeline shows question, reply, accepted with actors. Status `answered`. Employee deletes library file; expert downloads evidence and bytes match.
- Env: scratch DB `inquiry_e2e_scratch` on 5434; `alembic upgrade head` and `check` clean; uvicorn on 8011; Vite on 5190 (`BACKEND_URL` override). DB rows verified. Afterwards uvicorn stopped, DB dropped, temp worktree removed.
- Files: `frontend/e2e/inquiries.spec.ts` (new), `frontend/playwright.config.ts` (`inquiries` branch). No app code or `data-testid` changes; real labels sufficed.
- Command: `cd frontend && PLAYWRIGHT_SUITE=inquiries PLAYWRIGHT_PORT=5190 BACKEND_URL=http://127.0.0.1:8011 INQUIRY_E2E_DATABASE_URL=<scratch url> npx playwright test`

## Bugs

None found in the inquiry flow. Only expected 401s on `/auth/refresh` at unauthenticated page loads.

## Unresolved

1. Pass count is derived (720 - 15 - 23 - 33); unittest does not print it.
2. Port 5180 is held by another repo Vite (pid 6397). Not touched.
3. Phase 05 docs and roadmap updates not checked (out of scope).
