# Agent conversations

Scope: the implemented `/v1/conversations` API that lets a signed-in user chat
with the [standalone agent](../../agent/README.md). The backend stores the
conversation list (`conversations` table in the application database); the agent
stores the messages in its own `agent_memory` checkpoints. The agent supports
knowledge search with citations and photo input, but neither is connected yet
(see [Limits](#limits)), so answers currently cite no sources.

Owners: routes in `backend/api/conversations.py`, queries in
`backend/src/storage/conversations.py`, agent client in `backend/src/clients/agent.py`,
browser client in `frontend/src/services/conversations-api.ts`, live `/ask` page in
`frontend/src/pages/conversations.tsx`. Tests: `backend/tests/test_conversations.py`
(PostgreSQL), `backend/tests/test_conversation_streaming.py` (real HTTP), the
frontend tests next to those files and `frontend/e2e/qa.spec.ts`.

## Call sequence

1. The browser sends its in-memory access token (`Authorization: Bearer`; see
   [authentication](authentication.md)).
2. The backend authenticates the user, then checks that the conversation is
   active and owned by that user. Anything else returns `404` before the agent is called.
3. The backend closes its database session, then calls the agent with the shared
   service token (`AGENT_API_TOKEN`). The browser never receives that token.
4. The agent reads or writes the checkpoint whose thread ID is the conversation ID.

## Endpoints

Every route requires a bearer access token (`401` without one).

| Method and path | Body | Success |
|---|---|---|
| `GET /v1/conversations` | — | `200` `{conversations: [Conversation]}`, newest activity first, at most 200; no pagination yet |
| `POST /v1/conversations` | `{title}` | `201` `Conversation` |
| `PATCH /v1/conversations/{id}` | `{title}` | `200` `Conversation` |
| `DELETE /v1/conversations/{id}` | — | `204`; soft delete |
| `GET /v1/conversations/{id}/history` | — | `200` `{conversation_id, messages: [{role, text}], truncated}` |
| `POST /v1/conversations/{id}/stream` | `{message}` | `200` `text/event-stream` |
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
  `text` (provisional `delta`), then one final `complete` (`answer`) or `error`
  (`code`, `message`, `retryable`). See the [agent API](../../agent/docs/api.md#handle-the-stream).
- Replace provisional text with `complete.answer`; `complete` is sent only after
  the agent saved the answer. A stream that ends without a final event leaves the
  answer unconfirmed: reopen history to see what was saved.
- Closing the browser request closes the agent stream, which stops generation and
  frees the conversation for a new message. Nothing is resent automatically.
- History comes from the agent checkpoint: `{role, text, citations, attachment}`
  for saved user inputs and completed answers, oldest first, newest 200 messages
  at most (`truncated` marks omitted older ones). A failed turn shows the user
  input with no answer.
- `complete` and history carry the same saved `citations`
  (`{marker, evidence_id, title, excerpt, location}`); the UI links only those
  `[Sn]` markers and shows the excerpt. `complete.search` tells an empty search
  (`empty`) from a failed one (`unavailable`).
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

## Settings

| Variable | Purpose |
|---|---|
| `APP_DATABASE_URL` | Application database (same as authentication). |
| `AGENT_BASE_URL` | Agent address; native default `http://127.0.0.1:8010`. In Compose, `API_AGENT_BASE_URL` overrides the host default (`http://agent:8010` with `deployments/agent.compose.yml`). |
| `AGENT_API_TOKEN` | Must equal `AGENT_API_TOKEN` in `agent/.env`. |

Without `AGENT_BASE_URL` and `AGENT_API_TOKEN`, list/create/rename/delete work and
history/stream return `503`.

## Run locally

1. Apply migrations to the application database ([database migrations](../development/database-migrations.md)).
2. Set up and start the agent: `denso-agent setup-db`, then `denso-agent serve` ([agent setup](../../agent/README.md)).
3. Set `AGENT_BASE_URL` and `AGENT_API_TOKEN` in `.env`, then start the backend
   (`uv run uvicorn backend.app:app --port 8005`; on Windows add `--loop asyncio:SelectorEventLoop`).
4. Start the frontend in API mode and sign in; `/ask` lists your conversations.

## Limits

- No per-user limit on simultaneous answers: one user streaming in several chats
  can take all `AGENT_MAX_CONCURRENT_REQUESTS` slots, and others then get `429`.
- One agent process: the one-active-answer rule per conversation is enforced in
  that process only.
- Knowledge search is not connected: the agent has no live retrieval client and
  the backend sends no `search_scope` until the knowledge search API contract
  (authentication, fields, errors, source links) is agreed. Citations cannot open
  their source document yet.
- Photos are not connected: the agent accepts a validated photo per message, but
  the backend has no per-user files API to authorize and read one, and the
  configured model's image support is unverified.
- Speech-to-text has been tested against a controlled gateway only; no real
  Vietnamese recording has been checked, because the speech models run outside
  this laptop.
- The backend has no request-size limit: FastAPI stores a multipart upload in a
  temporary file before the route checks sign-in, so the 16 MB speech limit applies
  only after authentication (the older upload endpoints behave the same way).
- The legacy knowledge-base QA endpoint (`/v1/knowledge-agent/<kb>/stream`) still
  exists, but the live UI no longer shows it.
