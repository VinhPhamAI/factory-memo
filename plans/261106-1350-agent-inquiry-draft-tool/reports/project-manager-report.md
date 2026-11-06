# Project manager report — agent inquiry draft tool

Date: 2026-11-06. Branch: `feat/inquiry-workflow`. Nothing committed.

## Status

| Item | State |
|---|---|
| Phase 1 — backend endpoint | Superseded (session 1). No change. |
| Phase 2 — agent tools | Completed: code, unit tests, docs. Live-model check pending. |
| Phase 3 — chat display and docs | Completed: code, tests, docs. `git diff --check` and `cmp AGENTS.md CLAUDE.md` not yet run. |
| Live-model check (paid, once) | Not started. Blocked. |
| Commit | Not done. |

Plan status: implemented, live-model check pending. The plan is not finished.

## Edits made (plan folder only)

- `plan.md`: status line; phase table (phases 2 and 3 completed); two short notes (phase 2
  deviation, live-check blocker); risks extended with four open items. Validation Log untouched.
  About 103 lines.
- `phase-02-agent-tool.md`: rewritten to match the implementation. Tools table (update has no
  `inquiry_id`), behavior steps, deviations (a)–(d), create mapping with the photo-title and
  retry differences, file list, tests checklist (17 of 18 mapped, foreign-ID test removed),
  todo, success criteria, live-check blocker.
- `phase-03-chat-display-and-docs.md`: requirement and success checklists; known gap on UI
  draft button.
- `reports/project-manager-report.md`: this file.
- `phase-01-backend-endpoint.md`: not touched.

## Verification done

- Files exist: `runtime/inquiry_tools.py`, `runtime/history.py`, `clients/backend.py`,
  `clients/inquiries.py`, `tests/unit/test_inquiry_tool.py`, `chat-tools.test.ts`,
  `chat-tool-calls.test.tsx`.
- Tool and state names present in `schemas/tools.py` and `chat-tools.ts`.
- Test names in `test_inquiry_tool.py` match the 18 tests in the phase 02 list.
- Test, lint and build results are taken from the session record, not re-run.

## Remaining work (required to finish the plan)

1. Decide the live-check configuration: set `AGENT_KB_TOOLS_ENABLED` and forward the employee
   token, or accept a different test path. Provide a test login.
2. Start backend, worker and agent. Run the live check: create draft → ask again (expect
   `needs_choice`) → request an observation change (expect PATCH). Record results in phase 02.
3. Run `git diff --check` and `cmp AGENTS.md CLAUDE.md`.
4. Decide on open items below before commit. Then commit.

Completing the plan matters. Until the live check passes, the paid model path (forced tool
call) is unverified, and the draft flow is not proven end to end.

## Open items (from code review, accepted or unresolved)

- M1: draft saved in a failed turn is missing from history; a retry duplicates it. Fix needs
  backend discovery by conversation.
- L4: GET-then-PATCH overwrites a concurrent UI edit. File detach gives `422` → unavailable.
- L9: inquiry client reuses `AGENT_SEARCH_TIMEOUT_SECONDS`.

## Unresolved questions

- Should drafts from the UI draft button trigger `needs_choice`? Currently only agent-created
  drafts count.
- Is M1 acceptable for this release, or must the backend discovery fix land first?
- Which configuration path for the live check: enable KB tools in the backend container, or a
  different harness?
