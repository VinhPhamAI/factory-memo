# Phase 05 — Smoke test and docs

## Context links

- [Plan overview](plan.md) and phases 01–04
- Tests: `backend/tests/test_inquiries.py`, `frontend/e2e/` (`playwright.config.ts`,
  `PLAYWRIGHT_SUITE`), `backend/tests/test_library_knowledge_uploads.py` (library-delete pattern)
- Docs to touch: `docs/features/`, `docs/architecture/database-schema.md`,
  `docs/features/file-library.md`, `docs/features/conversations.md`,
  `docs/development/roadmap.md`, `docs/development/codebase-summary.md`

## Overview

- Priority: P1.
- Status: completed (2026-11-05). Smoke tests pass; docs written.
- Proves the whole flow once and documents implemented behavior.

## Requirements

Smoke sequence (backend integration test, then the same in Playwright):
1. Employee uploads a file to the library and creates a chat.
2. Draft from chat (controlled agent in the backend test); attach the file.
3. Submit to an expert.
4. Expert requests clarification; employee replies; expert accepts.
5. Assert:
   - Timeline is `question`, `reply`, `accepted` with the right actors; status `answered`.
   - `SELECT count(*) FROM knowledge_chunks` is unchanged from step 1 (zero new chunks).
   - Employee deletes the file from the library; requester and assignee can still
     download it through the inquiry; a third user gets `404`.
   - With the agent stopped (or the stub raising), the expert can still open the inquiry.

Docs:
- New `docs/features/inquiries.md`: scope, endpoints, transition table, errors,
  access rule, limits (no pagination, no notifications, citations empty until search lands).
- `database-schema.md`: `draft` status, nullable `assigned_to`, `submitted_at`.
- `file-library.md`: inquiry readers can open deleted files through the inquiry route.
- `conversations.md`: draft endpoint.
- `roadmap.md`: mark the inquiry milestone with the evidence above.
- `codebase-summary.md`: new entry points.
- Mark `plans/261103-1436-agent-conversations/phase-04-inquiry-draft.md` superseded.
- `docs/project-changelog.md` does not exist; do not create it unless the team asks.

## Related code files

Create: `docs/features/inquiries.md`, `frontend/e2e/inquiries.spec.ts`.
Modify: `backend/tests/test_inquiries.py`, docs listed above, `AGENTS.md` and
`CLAUDE.md` (add the inquiries link under "Read when relevant", keep both identical).

## Implementation steps

1. Add the backend smoke test (`test_inquiry_smoke_flow`).
2. Add the Playwright spec using two browser contexts (employee, expert); seed the
   expert role through the test database.
3. Write and update the docs.
4. Run the final checks.

## Todo list

- [x] Backend smoke test
- [x] Playwright smoke spec
- [x] Docs
- [x] Final checks (build, lint, tests; `git diff --check` clean except pre-existing `.gitignore:188`; `cmp AGENTS.md CLAUDE.md` identical)

## Success criteria

- Both smoke tests pass against PostgreSQL + pgvector.
- `uv run python -m unittest discover -s backend/tests -t . -v` shows no new failures
  (existing failures listed separately).
- Agent unit tests, `npm run build`, `npm run lint`, `npm run test` pass.
- `git diff --check` and `cmp AGENTS.md CLAUDE.md` are clean.

## Risk assessment

- Playwright needs the backend, database and frontend running; if the agent is not
  available in CI, the e2e draft step uses a manual draft via `POST /v1/inquiries`
  and the draft-from-chat step stays covered by backend tests. Record which path ran.

## Security considerations

- Test credentials stay in test fixtures; nothing populated is committed to `.env`.

## Next steps

Out of scope, later: pagination, notifications to experts, `in_review` on open,
`closed` by the requester, inquiry-to-knowledge promotion by explicit human review.
<!-- Updated: Validation Session 1 - deferred outcome reply and admin access -->
Also later: a requester `reply` after `answered` to report the repair outcome
(Flow 2 step 6), and any admin access to inquiries.
