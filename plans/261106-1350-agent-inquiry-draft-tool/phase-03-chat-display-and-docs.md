# Phase 03 — Chat display and docs

Priority: medium. Status: completed (code, tests, docs) 2026-11-06. Live-model check pending (see phase 02).

<!-- Updated: Implementation 2026-11-06 - card open by default; created, updated, needs-choice and not-draft states -->

## Requirements

- [x] `frontend/src/services/chat-tools.ts`: accepts `create_inquiry_draft` and
  `update_inquiry_draft`, states `needs_choice` and `not_draft`, `inquiry_id` (UUID) and `title`.
- [x] `frontend/src/components/chat-tool-calls.tsx`:
  - [x] create `results`: "Đã tạo nháp yêu cầu chuyên gia", title, link `/inquiries/<id>`.
  - [x] update `results`: "Đã cập nhật nháp", link.
  - [x] `needs_choice`: "Cuộc chat đã có nháp", link to the existing draft.
  - [x] `not_draft`: "Yêu cầu đã được gửi, không sửa được", link.
  - [x] Inquiry card open by default.
- [x] Tests for parsing and rendering in the existing test files
  (`chat-tools.test.ts`, `chat-tool-calls.test.tsx`).
- [x] Docs:
  - [x] `docs/features/inquiries.md`: agent tools; manual submit still required.
  - [x] `agent/docs/api.md`: tool event fields.
  - [x] `docs/features/conversations.md`: tool list.

## Success criteria

- [x] `npm run build` and `npm run lint` pass.
- [x] Targeted `npm run test` pass (25 tests).
- [x] `git diff --check` (clean except trailing spaces in the user's pre-existing
  `.gitignore` edit) and `cmp AGENTS.md CLAUDE.md` pass.
- [x] Live check (phase 02): real `tool` stream events carry the fields the card parses. Not seen in a browser (extension not connected).

## Known gaps

- Drafts made with the UI draft button do not trigger `needs_choice`. Only agent-created
  drafts count (open question, see `plan.md`).
