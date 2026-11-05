# Phase 04 — Inquiry UI

## Context links

- [Plan overview](plan.md); API contracts in phases [01](phase-01-schema-and-crud.md),
  [02](phase-02-review-actions.md), [03](phase-03-draft-from-chat.md)
- Routes: `frontend/src/main.tsx` (live shell `RequireAuth` + `QaShell`)
- Patterns: `frontend/src/services/conversations-api.ts` (client class, typed
  errors, Vietnamese messages), `frontend/src/services/file-api.ts`,
  `frontend/src/pages/files.tsx`, `frontend/src/pages/conversations.tsx`,
  `frontend/src/hooks/auth.tsx` (`useAuth().user.role`)
- UI kit: `frontend/src/components/ui/` (shadcn: button, dialog, select, textarea, badge, toast)

## Overview

- Priority: P1.
- Status: completed (2026-11-05). Nothing committed.
- Employee and expert inboxes, detail with evidence and timeline, review actions,
  and the "create inquiry" action in chat. Live mode only.

## Key insights

- No inquiry UI exists. `pages/card.tsx` (mock-only approval screen) is the
  closest layout reference; do not reuse its fixture logic.
- The detail page reads only `/v1/inquiries`, so it works with the agent offline.

## Requirements

Functional:
- Navigation link **Yêu cầu chuyên gia** → `/inquiries` with tabs:
  **Bản nháp** (sent, status draft), **Đã gửi** (sent, submitted),
  **Được giao** (assigned). Each row: title, status badge, other party, updated time.
- `/inquiries/:id`:
  - Draft (requester): editable title and four text fields; evidence list with
    remove; **Đính kèm file** picker from the user's library; expert `Select` from
    `GET /v1/experts`; **Lưu**, **Xóa nháp**, **Gửi**.
  - Submitted: read-only snapshot, evidence (citation excerpt or file download),
    timeline, and an action panel shown only when the current user and status allow:
    assignee on `open` → Chấp nhận / Sửa giải pháp / Từ chối / Hỏi thêm;
    requester on `needs_info` → Trả lời. Content-required actions open a textarea.
- Chat page: **Tạo yêu cầu chuyên gia** button calls the draft endpoint, shows a
  loading state, then navigates to `/inquiries/:id`.
- After an action, reload the inquiry; on `409` show "Trạng thái đã thay đổi" and reload.

Non-functional:
- All errors show Vietnamese toasts, same mapping style as `file-api.ts`.
- No role-based hiding replaces backend checks; the UI only mirrors them.

## Architecture

- `frontend/src/services/inquiries-api.ts`: `InquiriesApi` with `list(box)`, `get`,
  `update`, `remove`, `attachFile`, `detachFile`, `submit`, `addEvent`,
  `downloadFile`, `listExperts`, `draftFromConversation`.
- `frontend/src/pages/inquiries.tsx` (inbox), `frontend/src/pages/inquiry.tsx` (detail).
- `frontend/src/components/inquiry/` for `InquiryForm`, `EvidenceList`, `Timeline`, `ReviewActions`.
- One pure helper `allowedActions(inquiry, userId)` mirroring the transition table,
  unit-tested.

## Related code files

Create: the files above plus `*.test.ts(x)` next to them.
Modify: `frontend/src/main.tsx` (two routes), `frontend/src/components/qa-shell.tsx`
(nav link), `frontend/src/pages/conversations.tsx` (button).

## Implementation steps

1. `inquiries-api.ts` with tests for status → message mapping.
2. `allowedActions` helper with tests covering the transition table.
3. Inbox page and tests (tabs, empty states).
4. Detail page: draft editor, then read-only view, timeline, actions; tests.
5. Chat button; test that a failed draft shows an error and does not navigate.
6. `npm run build`, `npm run lint`, `npm run test -- src/pages/inquir src/services/inquiries`.

## Todo list

- [x] API client
- [x] `allowedActions`
- [x] Inbox
- [x] Detail (draft editor, view, timeline, actions)
- [x] Chat button
- [x] Build, lint, tests

## Success criteria

- An employee drafts from chat, edits, attaches a file, picks an expert and submits.
- The expert sees it under **Được giao**, opens evidence and completes a review.
- The employee sees the question and replies; the timeline shows every event in order.

## Risk assessment

- Without citations, the evidence list may be empty for most chats; the UI shows
  "Chưa có bằng chứng" and the file picker is the main way to add evidence.

## Security considerations

- Downloads go through the authorized fetch and a blob URL; no token in URLs.
- Event content renders as text, never as HTML.

## Next steps

Phase 5 covers the Playwright smoke run and documentation.
