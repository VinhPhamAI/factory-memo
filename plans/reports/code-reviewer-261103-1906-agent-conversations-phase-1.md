# Code review: agent conversations, phase 1 (uncommitted changes)

## Scope

- Branch `feat/agent-conversations`, working tree vs `HEAD` (plan files excluded).
- Agent: `api/routes.py`, `runtime/conversation.py`, `schemas/chat.py`, `config.py`.
- Backend: `api/conversations.py`, `src/clients/agent.py`, `src/storage/conversations.py`,
  `src/storage/app_database.py`, `src/storage/auth.py`, `api/auth.py`, `app.py`.
- Frontend: `services/conversations-api.ts`, `pages/conversations.tsx`, `main.tsx`, `index.css`.
- Checks run:
  - Agent unit tests: 66 passed.
  - `backend.tests.test_conversation_streaming`: 4 passed.
  - `backend.tests.test_conversations` (PostgreSQL): 12 passed.
  - Frontend vitest (2 files): 12 passed.
  - `tsc --noEmit` and eslint on the changed files: clean.
  - `cmp AGENTS.md CLAUDE.md`: identical. `git diff --check`: clean.
  - Playwright e2e: not run.

## Requirement verification

| Requirement | Result | Evidence |
|---|---|---|
| Every route uses `require_current_user` | Met | `CurrentUser` dependency is on all six routes (`backend/api/conversations.py:134-201`). |
| Ownership and active check before any agent call | Met | `require_active_owner` / `mark_active` run before `fetch_history` / `open_stream`. Tested in `test_foreign_conversation_and_bad_token_never_reach_the_agent`. |
| DB session released before the agent call | Met | `app_transaction` exits before the agent call. `require_current_user` holds no session across the request. |
| Stream forwarded without buffering | Met | Uses `aiter_raw()` and sends `X-Accel-Buffering: no`. The agent has no GZip middleware, so raw bytes are plain SSE. |
| Upstream closed on browser disconnect | Met | uvicorn 0.54 reports ASGI 2.3, so Starlette 1.10 cancels on disconnect. The `finally` in `ConversationStreamResponse.__call__` then runs `upstream.aclose()`. |
| 409/429 preserved before streaming | Met | The agent sends an `{"error": {...}}` envelope (`agent/app.py:74`). The client reads `error` and the backend re-emits `{"detail": {...}}`, which matches `docs/features/conversations.md:70-71`. |
| Service token not exposed or logged | Met | The token is set only in `AgentClient._headers`. Logs record only exception class names or HTTP status. httpx INFO logs do not include headers. |
| No raw exception text in responses or logs | Met | Agent `routes.py:55` logs the class name. `_agent_unavailable` logs messages the backend builds itself. |
| Soft delete keeps inquiry references | Met | `deleted_at` update; tested in `test_deleting_keeps_a_submitted_inquiry_reference`. |
| Provisional text replaced only by `complete.answer` | Met | `conversations.tsx:176-177`. |
| EOF without a final event is shown as unconfirmed | Met | `readEvents` yields `interrupted`, which the page shows as `unconfirmed`. |
| Switching chats aborts the stream; late events ignored | Mostly met | Holds during streaming. Not met while the conversation is still being created (see M1). |
| No automatic resend | Met | Retries only after a 401, which the backend returns before any agent call. |

## Critical

None found.

## High

### H1. A multi-line first message cannot start a new conversation
- Where: `frontend/src/pages/conversations.tsx:136` and `backend/api/conversations.py:34-43`.
- What happens: on the first send, the page creates the conversation with
  `title = message.slice(0, 80)`. `Title` rejects any Unicode `Cc` character, and
  `\n` and `\t` are `Cc` (checked: `TitleRequest(title='May A\nloi E12')` raises
  `ValidationError`). The composer allows Shift+Enter, so a typical fault description
  such as "Máy A\nlỗi E12" fails with 422 and the user sees
  "Không tạo được cuộc trò chuyện" or "Nội dung gửi không hợp lệ".
  Pasted logs and error tables hit this often.
- Why tests miss it: no test sends a newline in the first message.
- Fix: build the title from a single line, e.g. replace runs of whitespace with one
  space before slicing (`message.replace(/\s+/g, " ").slice(0, 80).trim()`).
  Optionally do the same on the server before validation. Add a page test with a
  multi-line first message.

## Medium

### M1. Navigating away while a new conversation is being created redirects the user back
- Where: `frontend/src/pages/conversations.tsx:134-172`.
- Failure scenario:
  1. On `/ask`, the user sends a first message and `api.create` is pending.
  2. The user opens conversation B with Back or a nav link. The select is disabled,
     but browser navigation still works.
  3. The effect aborts `controller` and calls `setSending(false)`.
  4. `create` resolves. `send()` continues without checking `abort.signal.aborted`:
     it sets `shown.current = id`, calls `navigate('/ask/<new>')` and moves the user
     off B, appends turns, then opens a stream with an already-aborted signal.
  5. The result is an empty orphan conversation, the user lands on the wrong page,
     and a "stopped" turn briefly appears.
- Related: the Stop button shown during `create` has no effect on the create request
  itself (no signal is passed).
- Fix: after `await api.create(...)`, return early if `abort.signal.aborted`, still
  calling `remember(created)` if you want it listed. Pass `abort.signal` to `create`.
  Add a test covering navigation during creation.

### M2. One user can use up all agent capacity
- Where: `backend/api/conversations.py:191-222`.
- What happens: the only limits are the agent's per-conversation lock (409) and its
  global `AGENT_MAX_CONCURRENT_REQUESTS` (429). A signed-in employee can create N
  conversations and stream in each at once, which returns 429 to every other user.
- Severity: this is an internal tool with authenticated users, so this is not a
  blocker for a demo.
- Fix: track active streams per user in the backend (in-process counter keyed by
  `user.id`, released in `ConversationStreamResponse.__call__`'s `finally`). Or
  accept and document the gap.

## Low

### L1. `updated_at` moves even when the agent rejects the message
- Where: `backend/api/conversations.py:211`.
- What happens: `mark_active` commits before `open_stream`. A 409, 429 or 503 still
  moves the conversation to the top of the list.
- Fix: keep the ownership check, but bump `updated_at` only after a 200 from the
  agent. Or accept and document it.

### L2. A stopped answer may already be saved
- Where: `frontend/src/pages/conversations.tsx:185`.
- What happens: the agent saves the checkpoint (`durability="sync"`) before it emits
  `complete`. If the user stops between those two points, the page shows "Đã dừng"
  with provisional text, but history contains the full answer.
- Fix: in the stopped note, offer the same "Tải lại cuộc trò chuyện" action as
  `unconfirmed`.

### L3. A single very long newest message empties the history
- Where: `agent/src/denso_agent/runtime/conversation.py:137-144`.
- What happens: if the newest public message alone exceeds
  `history_max_characters`, the loop stops at once and returns `messages=[]` with
  `truncated=true`. This cannot happen at the current defaults (20000-character input,
  400000-character limit), but a lowered setting would trigger it.
- Fix: always include at least the newest message, or document the behavior.

### L4. Conversations beyond the newest 200 show a permanent loading label
- Where: `frontend/src/pages/conversations.tsx:243-245`.
- What happens: an older conversation is outside the list (`LIST_LIMIT = 200`), so
  `current` stays undefined. The select shows "Đang tải…" indefinitely, and rename
  and delete stay disabled.
- Fix: fetch the conversation itself, or label the option neutrally.

### L5. Misleading startup warning
- Where: `backend/app.py:293-296`.
- What happens: when `APP_DATABASE_URL` is unset but `auth_service` is injected, the
  warning still says authentication is unavailable. This affects logs only.

### L6. Unexpected client errors surface as 500
- Where: `backend/api/conversations.py:214-221`.
- What happens: exceptions other than `httpx.HTTPError` from `open_stream` or
  `fetch_history` (for example `RuntimeError` from a closed client during shutdown)
  propagate as 500. This is acceptable, noted for completeness.

## Edge cases checked and found not to be issues

- **Tool or list-content `AIMessage` in history:** the graph has one node, no tools,
  and always writes `AIMessage(content=str)`. The `isinstance(content, str)` filter
  is enough.
- **Failed turn:** the agent checkpoints the `HumanMessage` input before the node
  runs, so a failed turn shows the user message without an answer. This is
  consistent with "no automatic resend".
- **Agent 128 KiB body limit:** httpx 0.28.1 encodes JSON with `ensure_ascii=False`,
  so 20000 code points of 4-byte UTF-8 is at most about 80 KB. No unexpected 413.
- **Exception handler order:** Starlette resolves handlers by MRO, so
  `AuthStorageUnavailable` keeps its auth-specific 503 message even though it now
  subclasses `AppDatabaseUnavailable`.
- **`app_transaction` exception wrapping:** `ConversationNotFound` is raised outside
  the `with` block, so it is not wrapped.
- **SSE parser:** handles CRLF, split chunks and multi-line `data:`. It cancels the
  body after a final event and ignores a partial trailing frame.
- **401 refresh:** `request()` retries once after a successful refresh. The
  JSON-string body can be sent again, and `authorizedFetch` merges headers.

## Recommended actions

1. Fix H1 (title made from one line) and add a test.
2. Fix M1 (check `aborted` after `create` and pass the signal) and add a test.
3. Decide on M2 (per-user concurrency) and L1, then implement or document them.
4. Optional: L2 to L4.

## Plan follow-ups

The phase-1 behaviors listed in the requirements are implemented and tested, except
for the H1 and M1 gaps. Plan files were not edited.

## Unresolved questions

- Should one user be allowed to hold several agent slots at once (M2)?
- Is it acceptable for rejected sends to reorder the list (L1)?
