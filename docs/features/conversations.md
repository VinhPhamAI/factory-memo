# Agent conversations

Scope: the implemented `/v1/conversations` API that lets a signed-in user chat
with the [standalone agent](../../agent/README.md). The backend stores the
conversation list (`conversations` table in the application database); the agent
stores the messages in its own `agent_memory` checkpoints. Knowledge-base tools
and citations are available when enabled in both services. See [settings](#settings).

Owners: routes in `backend/api/conversations.py`, queries in
`backend/src/storage/conversations.py`, agent client in `backend/src/clients/agent.py`,
browser client in `frontend/src/services/conversations-api.ts`, live `/ask` page in
`frontend/src/pages/conversations.tsx`. Tests: `backend/tests/test_conversations.py`
(PostgreSQL), `backend/tests/test_conversation_streaming.py` (real HTTP), the
frontend tests next to those files and `frontend/e2e/qa.spec.ts`. The draft route is
tested in `backend/tests/test_inquiry_draft_route.py`.

## Call sequence

1. The browser sends its in-memory access token (`Authorization: Bearer`; see
   [authentication](authentication.md)).
2. The backend authenticates the user, then checks that the conversation is
   active and owned by that user. Anything else returns `404` before the agent is called.
3. The backend closes its database session, then calls the agent with the shared
   service token (`AGENT_API_TOKEN`). With KB tools enabled, it also forwards
   the current employee access token for knowledge-base calls. The browser
   never receives the service token.
4. The agent reads or writes the checkpoint whose thread ID is the conversation ID.

## Endpoints

Every route requires a bearer access token (`401` without one).

| Method and path | Body | Success |
|---|---|---|
| `GET /v1/conversations` | — | `200` `{conversations: [Conversation]}`, newest activity first, at most 200; no pagination yet |
| `POST /v1/conversations` | `{title}` | `201` `Conversation` |
| `PATCH /v1/conversations/{id}` | `{title}` | `200` `Conversation` |
| `DELETE /v1/conversations/{id}` | — | `204`; soft delete |
| `GET /v1/conversations/{id}/history` | — | `200` `{conversation_id, messages, truncated}` with saved citations and tool summaries |
| `POST /v1/conversations/{id}/stream` | `{message}` | `200` `text/event-stream` |
| `POST /v1/conversations/{id}/inquiry-draft` | — | `201` Inquiry in status `draft`; see [draft an inquiry](#draft-an-inquiry) |
| `POST /v1/speech/transcriptions` | multipart `file` (PCM16 WAV, at most 120 s and 16 MB) | `200` `{text}` for the composer; nothing is stored |

`Conversation` is `{id, title, created_at, updated_at}`. A message the agent accepts, or
renaming moves `updated_at`.

| Field | Rule |
|---|---|
| `title` | Trimmed; 1–200 characters; no control characters. The UI uses the first 80 characters of the first message. |
| `message` | Trimmed; 1–20000 characters (the agent's limit). |
| Other fields | Rejected with `422`. |

## Stream and history

- The stream forwards the agent's SSE events unchanged and unbuffered: `status`,
  `reasoning` (model thinking, when the model reasons), `tool` (KB and inquiry-draft activity), `text` (provisional
  `delta`), then one final `complete` (`answer`) or `error`
  (`code`, `message`, `retryable`). See the [agent API](../../agent/docs/api.md#handle-the-stream).
- Replace provisional text with `complete.answer`; `complete` is sent only after
  the agent saved the answer. A stream that ends without a final event leaves the
  answer unconfirmed: reopen history to see what was saved.
- The UI shows reasoning in a collapsed "Đang suy nghĩ…" block that becomes
  "Đã suy nghĩ trong N giây" once the answer starts; clicking it shows the text.
  The current app session keeps thinking for up to 20 completed replies across
  navigation. Reloading or ending the session clears this memory. The agent
  never saves reasoning in history.
- Closing the browser request closes the agent stream, which stops generation and
  frees the conversation for a new message. Nothing is resent automatically.
- History comes from the agent checkpoint. Saved inputs and completed answers
  appear oldest first, newest 200 messages
  at most (`truncated` marks omitted older ones). A failed turn shows the user
  input with no answer. Completed replies can include a `reply_id` and `tools`.
- Tool cards show catalog and search progress, matches, and source references.
  Reopened cards use the saved summary and cited excerpts. Uncited excerpts
  are available only from the current stream. An empty search and an unavailable
  search have distinct statuses.
- Inquiry-draft tool cards open by default once the call finishes. They show the draft title
  and a link to the inquiry. The agent can create or edit a draft when asked in chat; it never submits one. States and
  limits: [chat tools](inquiries.md#draft-from-chat-with-agent-tools).
- The UI links validated `[Sn]` markers and result references through
  authenticated source routes. Removing the file from the personal library
  can leave an active KB source available to authorized users. Removing the
  KB source itself makes the source unavailable. Saved citation text remains.
- Speech: the composer's microphone records a clip, the backend transcribes it
  through the AI services gateway (`SPEECH_API_URL`, `POST /v1/audio/transcriptions`),
  and the text is placed in the composer for editing; the user sends it like typed
  text. `413` too long, `422` not a PCM16 WAV, `503` speech service unavailable.
- Deleting hides the conversation; the row, its agent checkpoint and inquiries
  that reference it are kept.

## Errors

| Status | Meaning |
|---|---|
| `401` | Missing or expired access token. The browser client refreshes once and repeats the request; the backend rejects these before the agent, so no message is sent twice. |
| `404` | Unknown, deleted or another user's conversation. |
| `409` | `{"detail": {"code": "conversation_busy", ...}}`: an answer for this conversation is still streaming. |
| `429` | `{"detail": {"code": "capacity_exceeded", ...}}`: the agent is at `AGENT_MAX_CONCURRENT_REQUESTS`. |
| `422` | Invalid ID or body. |
| `503` | Application database, agent or agent configuration unavailable. Details are logged by error class only. |

## Draft an inquiry

`POST /v1/conversations/{id}/inquiry-draft` asks the agent to draft an expert
inquiry from this conversation and saves it at once as a `draft`. Nothing is saved
when the draft fails. The steps, the copied evidence and the limits are in
[expert inquiries](inquiries.md#draft-from-chat).

A chat message can also save or edit a draft through agent tools. These run inside
the chat turn and include the message being sent. See
[chat tools](inquiries.md#draft-from-chat-with-agent-tools).

| Status | Body code | Meaning |
|---|---|---|
| `404` | — | Unknown, deleted or another user's conversation. The agent is not called. |
| `409` | `conversation_busy` | An answer or draft is already running here. |
| `422` | `empty_conversation` | The conversation has no messages. |
| `429` | `capacity_exceeded` | The agent is at capacity. |
| `502` | `draft_failed` | The model call failed, or the draft exceeds inquiry limits. |
| `503` | — | The agent is unreachable or returned an unusable response. |

Agent errors keep their status and body `{"detail": {"code", "message"}}`.

## Settings

| Variable | Purpose |
|---|---|
| `APP_DATABASE_URL` | Application database (same as authentication). |
| `AGENT_BASE_URL` | Native backend-to-agent address. The example uses `http://127.0.0.1:8010`. Set `API_AGENT_BASE_URL=http://agent:8010` when both run in Compose. |
| `AGENT_API_TOKEN` | Must equal `AGENT_API_TOKEN` in `agent/.env`. |
| `AGENT_KB_TOOLS_ENABLED` | Backend opt-in. Defaults to `false`. Set to `true` to forward the employee token, which enables the employee-scoped KB calls and the inquiry-draft chat tools. |
| `AGENT_KB_BACKEND_URL` | Native agent-to-backend address. The agent example uses `http://127.0.0.1:8005`. For Compose, override `AGENT_CONTAINER_KB_BACKEND_URL` if the default `http://api:8005` does not apply. |

Without `AGENT_BASE_URL` and `AGENT_API_TOKEN`, list/create/rename/delete work and
history/stream return `503`.

## Run locally

Run commands from the repository root unless a step specifies another directory.
Use Python 3.12+, `uv`, Docker with Compose, Node.js 22.12+, and Bash for the
migration script.

1. **Configure the backend.** On first setup, copy `.env.example` to `.env`.
   Keep the example local database and agent addresses when using the default
   ports. Generate a random `AGENT_API_TOKEN` and set
   `AGENT_KB_TOOLS_ENABLED=true` for local KB chat with a trusted agent.
   Configure `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, and `EMBEDDING_DIM` to
   match the embedding provider used by the KB. Supply its API key if required.
2. **Prepare PostgreSQL.** Run `uv sync`, then `bash deployments/migrate.sh`.
   The script starts local PostgreSQL and applies application migrations.
   See [database migrations](../development/database-migrations.md) for manual
   steps or an existing database.
3. **Start the agent.** Follow [agent setup](../../agent/README.md#local-setup)
   to create `agent/.env` and `agent/.env.setup`, set ModelArk credentials and
   database passwords, and run `setup-db` and `serve`. Set the same
   `AGENT_API_TOKEN` as the backend. Keep
   `AGENT_KB_BACKEND_URL=http://127.0.0.1:8005` for the native backend.
4. **Start the backend.** In a separate terminal, run
   `uv run uvicorn backend.app:app --host 127.0.0.1 --port 8005`. On Windows, add
   `--loop asyncio:SelectorEventLoop`. For a new upload, also start the
   [ingestion worker](knowledge-management.md#job-queue-and-workers). An
   already-ingested KB is sufficient to test chat search.
5. **Start the frontend.** From `frontend/`, run `npm ci` on first setup,
   then `npm run dev -- --port 5180 --strictPort`. The UI defaults to API mode.
   Open `http://127.0.0.1:5180/ask` and sign in.
6. **Try a known search.** Ask the agent to search a named, ingested KB for a
   term present in its sources. Expand the search row and open a citation.
   If the chat works but tool rows never appear, check both KB settings and
   restart the backend and agent after changing them.

## Limits

- No per-user limit on simultaneous answers: one user streaming in several chats
  can take all `AGENT_MAX_CONCURRENT_REQUESTS` slots, and others then get `429`.
- One agent process: the one-active-answer rule per conversation is enforced in
  that process only.
- Knowledge-base and inquiry-draft tool operations use the employee's current access
  token. If it expires mid-turn or the backend API is unavailable, the tool can report
  `unavailable`.
- A chat turn that fails after the backend saved an inquiry draft is not recorded in
  history, so the next draft request can create a second draft without asking. See
  [chat tools](inquiries.md#limits).
- The [personal file library](file-library.md) is available. Sending a library
  photo from chat still needs backend attachment handling and verification with
  the configured model.
- Speech-to-text has been tested against a controlled gateway only; no real
  Vietnamese recording has been checked, because the speech models run outside
  this laptop.
- The backend has no request-size limit: FastAPI stores a multipart upload in a
  temporary file before the route checks sign-in, so the 16 MB speech limit applies
  only after authentication (the older upload endpoints behave the same way).
- The legacy knowledge-base QA endpoint (`/v1/knowledge-agent/<kb>/stream`) still
  exists, but the live UI no longer shows it.
- Machine diagnosis is not an agent tool. With `VITE_MACHINE_DIAGNOSIS_VIZ=true`,
  the chat shows a front-end 3D diagnosis panel with synthetic values; see
  [3D machine diagnosis](machine-3d.md).
