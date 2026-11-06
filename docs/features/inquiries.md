# Expert inquiries

Scope: the implemented inquiry workflow. A signed-in employee turns a chat into a
self-contained inquiry (a fault description for a remote expert), edits it as a
draft, and submits it to an eligible expert. The expert accepts, edits, denies or
asks a question. The employee replies to a question. Every review action is kept as
a permanent event.

During a chat turn, the standalone agent can also save or edit the draft for the
employee. See [draft from chat with agent tools](#draft-from-chat-with-agent-tools).

Inquiries are not knowledge. No code path writes `knowledge_sources` or
`knowledge_chunks`. Accepting an inquiry does not approve reusable knowledge.

Not in scope here: admin access, notifications, pagination beyond 200 items per
list, reassignment, and recording a repair outcome after `answered`. See
[Limits](#limits).

Owners: routes in `backend/api/inquiries.py` (`/v1/inquiries`, `/v1/experts`) and
the draft route in `backend/api/conversations.py`; rules in
`backend/src/services/inquiries.py`; queries and locks in
`backend/src/storage/inquiries.py`; models in `backend/src/storage/orm/inquiries.py`;
migrations `da8178b423f6_add_inquiry_drafts.py` and
`7c3f9a1b5e20_add_inquiry_draft_rules.py`; agent client in
`backend/src/clients/agent.py` (also validates the agent's saved tool summaries, including inquiry tool states, when the history is proxied); agent draft in `agent/src/denso_agent/api/routes.py`,
`agent/src/denso_agent/runtime/inquiry_draft.py`,
`agent/src/denso_agent/schemas/inquiry_draft.py` and `agent/src/denso_agent/clients/modelark.py`; chat tools in
`agent/src/denso_agent/runtime/inquiry_tools.py` and `agent/src/denso_agent/clients/inquiries.py`;
chat card in `frontend/src/components/chat-tool-calls.tsx`;
pages in `frontend/src/pages/inquiries.tsx` and `frontend/src/pages/inquiry.tsx`;
components in `frontend/src/components/inquiry/`; client in
`frontend/src/services/inquiries-api.ts`.

## Setup

1. Apply the migrations: `uv run alembic upgrade head`, then `uv run alembic check`
   ([database migrations](../development/database-migrations.md)).
2. Drafting from a chat needs the agent: set `AGENT_BASE_URL` and `AGENT_API_TOKEN`
   ([conversations](conversations.md#settings)). Without the agent, the draft-from-chat
   route returns `503`. Drafts created with `POST /v1/inquiries` still work.
3. Chat tools need the agent's knowledge base address (`AGENT_KB_BACKEND_URL`) and
   the backend's `AGENT_KB_TOOLS_ENABLED=true`. The backend forwards the employee token
   only when that flag is on, so without it the tools are not offered. Each backend request
   from a chat tool uses the agent's `AGENT_SEARCH_TIMEOUT_SECONDS` limit (default 10 seconds).

## Statuses

| Status | Meaning | UI label |
|---|---|---|
| `draft` | Private to the requester. No assignee. | Bản nháp |
| `open` | Submitted; waiting for the assigned expert | Đang chờ chuyên gia |
| `needs_info` | The expert asked a question; waiting for the requester | Cần bổ sung |
| `answered` | The expert accepted or revised the solution. Final. | Đã trả lời |
| `closed` | The expert denied the inquiry. Final. | Đã từ chối |
| `in_review` | Defined in the schema; no action sets it. | — |

## Transitions

Any other combination of status, action and actor returns `409 invalid_transition`
and writes nothing.

| From | Action | Actor | To | Event kind | Content |
|---|---|---|---|---|---|
| `draft` | Submit | Requester | `open` | none; sets `submitted_at` | Title and situation required |
| `open` | Accept | Assignee | `answered` | `accepted` | Optional |
| `open` | Edit the solution | Assignee | `answered` | `edited` | Required |
| `open` | Deny | Assignee | `closed` | `denied` | Required |
| `open` | Ask a question | Assignee | `needs_info` | `question` | Required |
| `needs_info` | Reply | Requester | `open` | `reply` | Required |

Event kinds accepted by `POST /v1/inquiries/{id}/events`: `accepted`, `edited`,
`denied`, `question`, `reply`. The database also allows `closed`; no action writes it.

## Access rule

| Caller | Draft | Submitted (any other status) |
|---|---|---|
| Requester | Read, edit, attach or detach files, delete, submit | Read; reply when `needs_info` |
| Assignee | Not visible (`404`) | Read; review actions when `open`; download evidence |
| Anyone else, including admins | `404` | `404` |

A missing, deleted and forbidden inquiry all return the same `404`, so IDs cannot
be probed. The access check runs in the same query as the read or write.

## Endpoints

Every endpoint requires `Authorization: Bearer <access_token>` (see [authentication](authentication.md)).
Error bodies use `{"detail": ...}`. Inquiry rule errors use `{"detail": {"code", "message"}}`.

| Method and path | Body | Success | Errors |
|---|---|---|---|
| `POST /v1/inquiries` | `{conversation_id, title?, context?, file_ids?}` | `201` Inquiry | `404` conversation not found; `422` `unreadable_file`, `invalid_context`, invalid body |
| `GET /v1/inquiries?box=sent\|assigned` | — | `200` `{inquiries: [Summary]}` | `422` invalid `box` |
| `GET /v1/inquiries/{id}` | — | `200` Inquiry | `404`, `422` malformed ID |
| `PATCH /v1/inquiries/{id}` | `{title?, context?}` | `200` Inquiry | `404`; `409` `invalid_transition`; `422` `invalid_context`, invalid body |
| `DELETE /v1/inquiries/{id}` | — | `204` soft delete | `404`; `409` `invalid_transition` |
| `PUT /v1/inquiries/{id}/files/{file_id}` | — | `204` attached | `404`; `409` `invalid_transition`; `422` `unreadable_file`, `invalid_context` (more than 20 files) |
| `DELETE /v1/inquiries/{id}/files/{file_id}` | — | `204` detached | `404`; `409` `invalid_transition` |
| `POST /v1/inquiries/{id}/submit` | `{assigned_to}` | `200` Inquiry, status `open` | `404`; `409` `invalid_transition`; `422` `invalid_context`, `ineligible_assignee` |
| `POST /v1/inquiries/{id}/events` | `{kind, content?}` | `200` Inquiry | `404`; `409` `invalid_transition`; `422` `content_required` |
| `GET /v1/inquiries/{id}/files/{file_id}/content` | — | `200` original bytes, `attachment` | `404` (not visible, not attached, or bytes missing) |
| `GET /v1/experts` | — | `200` `{experts: [{id, display_name}]}`, sorted by name | `503` |
| `POST /v1/conversations/{id}/inquiry-draft` | — | `201` Inquiry (status `draft`) | See [draft from chat](#draft-from-chat) |

`GET /v1/inquiries` returns at most 200 items per box, most recently updated first.
The `sent` box includes drafts. The `assigned` box never includes drafts.

Inquiry fields: `id`, `title`, `status`, `requested_by`, `requester_name`,
`assigned_to` (`null` only while `draft`), `assignee_name`, `created_at`,
`updated_at`, `submitted_at`. A full Inquiry adds `conversation_id`, `context`,
`files` and `events`. Each event has `id`, `kind`, `content`, `actor` (`{id, display_name}`)
and `created_at`. A file has `file_id`, `filename`, `content_type`, `size_bytes`
and `event_id` (always `null` today). The storage location is never returned.

`PATCH` with `context` replaces the whole context object. Omitted fields stay as they are.

### Error codes

| Code | HTTP | Raised when |
|---|---|---|
| `invalid_transition` | `409` | The action is not allowed for the status or the caller's role, or a draft-only action targets a submitted inquiry |
| `ineligible_assignee` | `422` | The expert is not an active, undeleted account with role `expert`, or is the requester |
| `invalid_context` | `422` | Submit without title or situation; evidence refers to a file that is not attached; more than 20 files; stored context is malformed |
| `unreadable_file` | `422` | A file is unknown, deleted, or not the requester's |
| `content_required` | `422` | `edited`, `denied`, `question` or `reply` without non-blank content |
| `draft_failed` | `502` | The agent's draft exceeds inquiry limits. Nothing is saved. |
| `empty_conversation` | `422` | The conversation has no messages. Returned by the agent; no model call. |
| `conversation_busy` | `409` | An answer or draft is already running in this conversation |
| `capacity_exceeded` | `429` | The agent is at `AGENT_MAX_CONCURRENT_REQUESTS` |

The agent errors come through the draft route with their status. An unreachable
agent, an agent `401`, or an invalid agent response gives `503`.

## Context fields and limits

| Field | Rule |
|---|---|
| `title` | Trimmed, up to 200 characters. May be empty in a draft; required to submit. |
| `context.situation` | Up to 4000 characters. Required to submit. |
| `context.observations`, `attempted_actions`, `proposed_fix` | Up to 4000 characters each. Optional. |
| `context.evidence` | Up to 50 items. |
| Evidence `kind` | `citation` or `file` |
| Evidence `title` | Up to 300 characters |
| Evidence `excerpt` | Up to 4000 characters |
| Evidence `location` | Up to 300 characters |
| Evidence `evidence_id` | Up to 200 characters |
| Evidence `file_id` | Must be attached to the inquiry |
| Attached files | Up to 20 per inquiry |
| Event `content` | Up to 4000 characters |

Limits for drafts built from the agent output: the title is clipped to 200 characters,
excerpts to 4000 and locations to 300. A citation with an `evidence_id` over 200
characters is skipped, not shortened. Text fields are not clipped; over-long text fails
with `draft_failed`.

## Evidence after the library file is deleted

- Attaching a file copies its reference into the inquiry. The inquiry does not depend
  on the library entry.
- Deleting the file from `/v1/files` only sets `deleted_at`. The bytes stay on disk
  ([file library](file-library.md#deletion)).
- The requester and the assignee can still download the file through
  `GET /v1/inquiries/{id}/files/{file_id}/content`. The response is the original bytes.
- Other users get `404`.
- If the bytes are missing from disk, the download returns `404`.

## Draft from chat

`POST /v1/conversations/{id}/inquiry-draft` drafts an inquiry from a saved chat and
stores it at once as a `draft`. The user deletes it if it is not wanted.

1. The browser sends its access token. The backend checks that the conversation is
   active and owned by the caller. Otherwise it returns `404`, and the agent is not called.
2. The backend calls the agent `POST /v1/agent/conversations/{id}/inquiry-draft` with the
   service token (`AGENT_API_TOKEN`). The wait limit is 150 seconds.
3. The agent reserves the conversation, as for a chat turn. A second request in the
   same conversation gets `409 conversation_busy`. A full agent gets `429 capacity_exceeded`.
4. The agent loads the saved history. Empty history returns `422 empty_conversation`
   with no model call.
5. The agent makes one model call that must call the `write_inquiry_draft` tool. A
   missing tool call, invalid arguments, or a provider error returns `502 draft_failed`.
6. The agent returns the drafted fields, the citations and the photos from the history.
   It saves nothing.
7. The backend keeps the photos the requester can still read (up to 20). Each kept
   photo also becomes a file evidence item. It adds citations up to the 50-item limit.
8. The backend saves the draft and its files in one transaction. It returns `201`
   with the Inquiry. The browser opens `/inquiries/{id}`.

The model reads the transcript as data, not instructions. The prompt forbids invented
measurements, actions and outcomes. The draft window is the agent history window:
the newest 200 messages or 400,000 characters (`AGENT_HISTORY_MAX_MESSAGES`,
`AGENT_HISTORY_MAX_CHARACTERS`). Older messages are left out without a notice in the response.

## Draft from chat with agent tools

During a chat turn, the agent can save or edit a draft through two tools. The tools
run only when the employee explicitly asks. They use the employee's own access token,
so the employee's permissions apply.

| Path | Trigger | Includes the message being sent | Saves |
|---|---|---|---|
| Draft button: `POST /v1/conversations/{id}/inquiry-draft` | **Tạo yêu cầu chuyên gia** on `/ask` (see [draft from chat](#draft-from-chat)) | No. Saved history only. | At once |
| `create_inquiry_draft` tool | The employee asks in chat to send the problem to an expert or to create an inquiry | Yes | At once, unless the conversation already has a draft |
| `update_inquiry_draft` tool | The employee names the change to an existing draft | Yes | The named fields |

The agent offers `update_inquiry_draft` only while the conversation's latest
agent-created inquiry is still `draft`. The tool description and prompt allow it only when
the employee explicitly asks to edit the draft and names the change.

**When the tools make backend requests**

The agent makes an inquiry request on a turn only in these cases:

- An earlier turn created a draft. The agent reads the latest one before routing and,
  while it is still `draft`, shows its fields to the routing model.
- The model calls `create_inquiry_draft` without `focus`, and an earlier turn created a
  draft. The agent reads that draft before deciding. The read is skipped when the draft
  was already read before routing.
- An inquiry tool runs. This is the save or edit request itself.

A conversation in which the agent never created a draft makes no inquiry request.

The inquiry rules are added to the system prompt only when the tools are offered. The
answer may say a draft was saved or edited only when the tool outcome reports `draft_saved`
or `draft_updated`. The drafting history is built inside the create tool.

| Tool | Arguments | Effect |
|---|---|---|
| `create_inquiry_draft` | `focus` (optional, up to 500 characters): the one problem the new draft covers | Saves a new draft |
| `update_inquiry_draft` | Optional `title` (up to 200 characters), `situation`, `observations`, `attempted_actions`, `proposed_fix` (each up to 4000). Pass only the changed fields, in full. | Replaces the named fields of the latest agent-created draft. Other fields and evidence stay. |

Outcomes, shown as the tool `state` in the chat card:

| State | Meaning | Saved |
|---|---|---|
| `results` | Draft created or updated. The card links to `/inquiries/<id>`. | Yes |
| `needs_choice` | The conversation already has a draft. The answer shows its title and asks whether to edit it or create a new one, and which changes to make. | No |
| `not_draft` | The draft was already submitted. The edit is refused: either the status read is no longer `draft`, or the backend answers `409`. | No |
| `unavailable` | The model call, validation, backend call or time limit failed. The answer still appears. | No |

**Create**

1. A forced model call writes the title and text fields from the bounded conversation
   history, including the new message. "Forced" means the model must call the
   `write_inquiry_draft` tool and cannot reply in plain text.
2. The agent maps the draft to the backend limits:
   - Citations from earlier answers become `citation` evidence. A citation whose
     ID is longer than 200 characters is skipped.
   - Photos from the chat become `file_ids` (up to 20) and `file` evidence. Each file
     evidence item is titled "Ảnh đính kèm" (attached photo). The backend instead uses the
     library filename, because the agent does not know it.
   - Photo slots are reserved first. Citations fill the rest, up to 50 items in total.
     Photo evidence is listed after the citations.
3. The agent calls `POST /v1/inquiries` as the employee.
4. If the backend answers `422` while photos are attached, the agent retries once
   without any photos. The backend's own mapping skips only the unreadable file; the
   agent drops all photos for that retry.
5. The answer tells the employee to open the draft, choose an expert and submit it.

**Second request in the same conversation**

- Without `focus`, the create tool looks for the latest draft that an earlier turn created
  (IDs from saved tool results in the chat history), unless the draft was already read
  before routing.
- `create_inquiry_draft` saves nothing and returns `needs_choice`, unless the previous answer
  already reported `needs_choice` and the call names the new problem in `focus`. Only then
  does it save a separate draft that covers that problem.
- If that draft was submitted or deleted, the tool creates a new draft without asking.

**Update**

1. The agent reads the current inquiry, so edits made in the UI are kept.
2. If the status is no longer `draft`, the result is `not_draft`.
3. The agent sends `PATCH /v1/inquiries/{id}`. A title-only edit sends only `title`. A text
   edit sends the whole context it read in step 1, with the named fields replaced and the
   evidence unchanged. An edit saved in the UI between steps 1 and 3 is overwritten (see
   [Limits](#limits)).

A call with no editable draft, or with no changed fields, returns `unavailable` with no search.

**Boundaries**

- The tools never submit an inquiry and never choose an expert. Submission stays on the inquiry page.
- Chat history and tool events keep only `inquiry_id` and `title`, never the draft text.
  See [agent API](../../agent/docs/api.md#inquiry-draft-tools).
- A turn makes at most one inquiry tool call.

## Frontend

| Route | Page | What the user does |
|---|---|---|
| `/inquiries` | `inquiries.tsx` | Three tabs: **Bản nháp** (drafts), **Đã gửi** (sent, submitted), **Được giao** (assigned to me). Each row shows its status label. |
| `/inquiries/:id` | `inquiry.tsx` | Requester on a draft: edit fields, attach library files (**Đính kèm file**), choose an expert (**Chuyên gia**), submit (**Gửi**), delete (**Xóa nháp**). Anyone on a submitted inquiry: read the timeline and download evidence (**Tải về**). Assignee on `open`: **Chấp nhận**, **Sửa giải pháp**, **Từ chối**, **Hỏi thêm**. Requester on `needs_info`: **Trả lời**. |

The sidebar entry **Yêu cầu chuyên gia** links to `/inquiries`. The chat page
(`/ask`) has **Tạo yêu cầu chuyên gia**, which calls the draft-from-chat route. A chat tool
card (`chat-tool-calls.tsx`) opens by default once the call finishes, and shows the draft title. Its link reads **Mở nháp để chọn chuyên gia và gửi**
(open the draft to choose an expert and submit it) after a created draft, and **Mở yêu cầu**
(open the inquiry) otherwise.

The UI shows only the actions that `allowedActions` returns. The backend still decides.
Stale actions return `409`, and the page shows **Trạng thái đã thay đổi** and reloads.

## Tests

| Check | Command | Needs |
|---|---|---|
| Backend, offline cases | `uv run python -m unittest backend.tests.test_inquiries backend.tests.test_inquiry_draft_route backend.tests.test_inquiry_smoke -v` | Nothing |
| Backend, PostgreSQL cases | Same command | `TEST_DATABASE_URL` pointing to a local test database with `pgvector` |
| Agent draft | `uv run --project agent pytest -c agent/pyproject.toml agent/tests/unit/test_inquiry_draft.py -q` | Nothing |
| Frontend unit | From `frontend/`: `npm run test -- src/pages/inquiries.test.tsx src/services/inquiries-api.test.ts` | Nothing |
| Agent chat tools | `uv run --project agent pytest -c agent/pyproject.toml agent/tests/unit/test_inquiry_tool.py -q` | Nothing |
| Frontend chat cards | From `frontend/`: `npm run test -- src/components/chat-tool-calls.test.tsx src/services/chat-tools.test.ts src/pages/conversations.test.tsx` | Nothing |
| Browser smoke test | From `frontend/`: `PLAYWRIGHT_SUITE=inquiries npx playwright test` | Running API and a scratch database (below) |

Test files: `backend/tests/test_inquiries.py` (rules, access, transitions, races),
`backend/tests/test_inquiry_draft_route.py` (draft from chat, controlled agent),
`backend/tests/test_inquiry_smoke.py` (draft, submit, question, reply, accept, evidence
after library delete, agent offline), `agent/tests/unit/test_inquiry_draft.py`,
`frontend/src/pages/inquiries.test.tsx`, `frontend/src/services/inquiries-api.test.ts`,
`frontend/e2e/inquiries.spec.ts`, `agent/tests/unit/test_inquiry_tool.py` (chat tools),
`frontend/src/components/chat-tool-calls.test.tsx`, `frontend/src/services/chat-tools.test.ts` and
`frontend/src/pages/conversations.test.tsx` (chat cards).

PostgreSQL tests are skipped without `TEST_DATABASE_URL`. The browser test:

1. Start the API against a scratch database. The name must contain `scratch` and must
   not be `voice_agent` or `voice_agent_app`.
2. Set `INQUIRY_E2E_DATABASE_URL` to the same database, in the form
   `postgresql+psycopg://user:password@host:port/<name>_scratch`.
3. Run `PLAYWRIGHT_SUITE=inquiries npx playwright test`.

The test signs up an employee and an expert. It grants the expert role with a
database update, which must match one row. It creates the draft with
`POST /v1/inquiries`, because the agent is not running in this test. Draft-from-chat
is covered by the backend tests.

Last recorded run (2026-11-05, working tree): backend inquiry modules 39 passed with
`TEST_DATABASE_URL` (5 offline, 34 PostgreSQL); agent draft unit tests 15 passed;
frontend inquiry tests 25 passed; browser smoke test 1 passed against a scratch
database. The pinned tool call has not been checked against a live model.
Chat tool tests (2026-11-06, working tree): agent unit suite 154 passed (`test_inquiry_tool.py`:
25 tests); backend `test_conversations` history projection covers inquiry tool states;
frontend chat tool tests 25 passed across `chat-tool-calls.test.tsx`, `chat-tools.test.ts`
and `conversations.test.tsx`. Live-model check passed 2026-11-06 through the backend API
(create, edit-or-new question, field edit).

## Limits

- Lists have no pagination. Each box returns at most 200 items.
- No notifications. Experts see new inquiries only by opening the **Được giao** tab.
- `in_review` is defined but unused.
- No outcome reply after `answered`. The requester cannot report a repair result.
  This is later work.
- Admins have no special access. Only the requester and the assignee can open an inquiry.
- Citations come only from saved chat answers that cite knowledge search results.
  Other draft evidence is chat photos, plus library files attached in the UI
  ([conversations](conversations.md#limits)).
- The pinned `tool_choice` for `write_inquiry_draft` is unverified on the live model.
  The unit tests use a fake provider. If the provider rejects a pinned choice, drafts fail with `draft_failed`.
  The chat tools use the same forced call.
- The history window bounds the draft. Older messages are dropped without a notice to the user.
- No reassignment, no `closed` action by the requester, and no draft cleanup other than
  deletion. Deleted drafts remain in the table, hidden.
- `inquiry_files.event_id` is never set, so file attachments are not linked to events.
- Chat `update_inquiry_draft` reads the draft, then writes the whole context. A UI edit saved
  between the two requests is overwritten.
- If reading the latest draft fails or times out, the chat tool treats the conversation as
  having no draft. A second `create_inquiry_draft` can then add a second draft.
- A turn that fails after the backend saved the draft leaves no record in chat history. The
  turn fails when the answer errors, the deadline passes, the checkpoint write fails, or the
  client times out after the backend commits. The next create request then saves another draft
  without asking, because the tool finds drafts only in saved history.
- The agent repeats the evidence mapping of `create_draft_from_agent` in
  `backend/src/services/inquiries.py`. Change both together.
