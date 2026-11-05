# API contract

## Call the agent

`POST /v1/agent/stream`

```text
Authorization: Bearer <AGENT_API_TOKEN>
Content-Type: application/json
```

```json
{"conversation_id":"11111111-1111-4111-8111-111111111111","message":"Explain the service scope."}
```

| Field | Rule |
|---|---|
| `conversation_id` | Required UUID (unique conversation ID), chosen by the caller; reuse for follow-ups. |
| `message` | Required string; trimmed; 1–20000 characters. |
| `search_scope` | Optional `{"knowledge_bases": [string, ...]}` (1–50). The caller can narrow the employee's authorized KB scope. |
| `attachment` | Optional photo for this message: `{"file_id", "media_type", "data"}`. `file_id` (1–200 characters) is the caller's reference, saved with the message. `media_type` is `image/jpeg`, `image/png` or `image/webp`. `data` is strict base64, at most 3 MiB decoded, sent to the model for this turn only and never saved. |
| Other fields | Rejected. |
| Request body | Maximum 5 MiB, including chunked requests. |

**Caller responsibilities**

- Authorize user login and conversation ownership.
- Keep the shared service token out of browsers. It is not a user JWT (login token).
- For KB tools, forward the authenticated employee's access token in the internal
  `X-Employee-Access-Token` header. The agent uses it only for the backend's
  knowledge APIs and keeps it out of model input, events, and history.
- Use the server-generated request ID for tracing, not duplicate prevention.

Authentication rejects before provider or checkpoint execution.

## Handle the stream

Server-Sent Events (SSE): UTF-8 frames separated by blank lines, with `event:`
names and JSON `data:`. Caching and proxy buffering are disabled.

Buffer incomplete frames; one network read may contain partial or multiple frames.
Every event has `request_id`, `conversation_id`, and `sequence` (increasing from 1
within the request).

| Event | Additional fields | Client action |
|---|---|---|
| `status` | `state: "generating"` | Show that the request was accepted. |
| `reasoning` | `delta: string` | Append to a separate, collapsed "thinking" view. The provider's unverified reasoning stays separate from the answer and is capped at 64000 characters per answer. It is absent from saved history. |
| `tool` | `tool: ToolActivity` | Update the current reply's tool card by `tool.id`. |
| `text` | `delta: string` | Append provisional text. |
| `complete` | `finish_reason: "stop"`, required `answer: string`, `citations`, `search`, optional `reply_id` and `tools` | Replace provisional text with the whole saved answer. Link its citations and reconcile the live tool cards with the saved summary. |
| `error` | `code`, `message`, `retryable` | Mark the answer as unfinished. |

```text
event: text
data: {"request_id":"22222222-2222-4222-8222-222222222222","conversation_id":"11111111-1111-4111-8111-111111111111","sequence":2,"delta":"This service maintains conversations."}

event: complete
data: {"request_id":"22222222-2222-4222-8222-222222222222","conversation_id":"11111111-1111-4111-8111-111111111111","sequence":3,"finish_reason":"stop","answer":"This service maintains conversations."}

```

**Completion rules**

- `complete.answer` is the assistant message after graph execution and final checkpoint saving succeed.
- Replace accumulated text; never append the full answer. Unprocessed fragments do not affect this value.
- One final event per connected request: `complete` or `error`.
- No `complete` means unfinished. HTTP 200 alone does not confirm success.
- Disconnections can lose the final event. No replay exists; read [saved history](#read-saved-history) to see whether the answer was saved.
- Clients correlate optional `reply_id` with saved history and accept older replies without tool summaries.

## Handle errors

### Before streaming

```json
{"error":{"code":"unauthorized","message":"Authentication required."}}
```

| HTTP status | Code | Meaning |
|---|---|---|
| 401 | `unauthorized` | Missing or invalid service token. |
| 413 | `payload_too_large` | Body exceeds 5 MiB. |
| 422 | `invalid_request` | Invalid JSON, UUID, message, or extra fields. |
| 409 | `conversation_busy` | This conversation already has an active request. |
| 429 | `capacity_exceeded` | Global request capacity is full. |
| 503 | `unavailable` | Runtime is not ready. |

### After streaming starts

HTTP status stays 200; read the `error` event.

Codes: `timeout`, `provider_error`, `context_limit`, `output_limit`,
`filtered_output`, `empty_output`, `persistence_error`.

- `retryable` permits a fresh manual attempt; duplicate-free execution is not guaranteed.
- No automatic retry or replay occurs.
- Length-limited, filtered, empty, and unfinished answers never complete successfully.
- Raw exceptions, provider error bodies and credentials are excluded. Model reasoning is sent only as `reasoning` events.

## Knowledge search and citations

When the backend enables KB tools and the agent has a KB backend URL, the model
can call `list_knowledge_bases` without arguments or `search_knowledge` with a
KB name and a query (1–500 characters). Each turn runs at most one list and one
search. The backend authorizes each KB request using the employee access token.

| Rule | Behavior |
|---|---|
| Evidence | At most `AGENT_SEARCH_MAX_RESULTS` (5) results, excerpts cut to `AGENT_SEARCH_MAX_EXCERPT_CHARACTERS` (1500), numbered `S1`, `S2`, ... and given to the answer call as data, not instructions. |
| Deadline | `AGENT_SEARCH_TIMEOUT_SECONDS` (10) per KB operation. |
| `search` | `not_used`, `results`, `empty` (nothing matched) or `unavailable` (search failed or timed out, or the model's call was unusable). |
| `citations` | Each valid `[Sn]` marker carries `marker`, `evidence_id`, `title`, `excerpt`, `location`, and source references when available. Invented markers stay plain text. |
| `ToolActivity` | `id`, `name`, and `state` (`running`, `results`, `empty`, or `unavailable`). The card can also carry `kb`, `query`, `count`, `truncated`, `catalog`, and ranked `results` with source references. |
| Retention | The final answer saves its citations and a bounded tool summary. Uncited search excerpts are available only in the live tool event. Internal provider routing messages stay private. |

See [conversation settings](../../docs/features/conversations.md#settings) to
enable the backend connection.

## Read saved history

`GET /v1/agent/conversations/{conversation_id}/history` with the same
`Authorization: Bearer <AGENT_API_TOKEN>` header.

```json
{"conversation_id":"11111111-1111-4111-8111-111111111111","messages":[{"role":"user","text":"Explain the service scope."},{"role":"assistant","text":"This service maintains conversations."}],"truncated":false}
```

| Rule | Behavior |
|---|---|
| Source | The conversation's latest checkpoint; no separate message table. |
| Messages | Saved user inputs and completed assistant answers, oldest first. Each has `role`, `text`, `citations`, and `attachment` (photo reference or `null`). Assistant replies can carry `reply_id` and `tools`. Raw tool messages, system messages, and reasoning are excluded. |
| Unknown conversation | HTTP 200 with an empty list. The caller checks ownership first. |
| Failed turn | The saved user input appears without an answer; provisional text never appears. |
| Limit | Newest messages within `AGENT_HISTORY_MAX_MESSAGES` (200) and `AGENT_HISTORY_MAX_CHARACTERS` (400000); `truncated` is true when older messages were left out. |
| Errors | 401 `unauthorized`, 422 `invalid_request` (not a UUID), 503 `unavailable` (runtime or checkpoint read failed; never an empty list). |

## Draft an inquiry

`POST /v1/agent/conversations/{conversation_id}/inquiry-draft` with the same service
token and no body. The backend calls it only after it confirms the user owns the
conversation. The agent makes one model call, which must call `write_inquiry_draft`,
then returns the draft. It saves nothing.

```json
{"fields":{"title":"Pump vibrates","situation":"Cooling pump vibrates at start-up.","observations":"","attempted_actions":"","proposed_fix":""},"citations":[],"attachments":[{"file_id":"22222222-2222-4222-8222-222222222222","media_type":"image/jpeg"}]}
```

| Rule | Behavior |
|---|---|
| Fields | `title` (up to 200 characters, may be empty), and `situation`, `observations`, `attempted_actions`, `proposed_fix` (each up to 4000 characters, may be empty). |
| Evidence | `citations` and `attachments` are copied from the saved history, first seen first. The model does not write them. |
| Concurrency | The conversation is reserved like a chat turn. A running turn or draft gives 409; a full agent gives 429. |
| History | Same window as the history endpoint. Older messages are not sent to the model. |
| Model failure | 502 `draft_failed` for a provider error or timeout, no tool call, invalid arguments, or fields out of range. |
| Errors | 401 `unauthorized`; 409 `conversation_busy`; 422 `empty_conversation` (no model call) or `invalid_request` (not a UUID); 429 `capacity_exceeded`; 502 `draft_failed`; 503 `unavailable` (no provider, or history read failed). |

The forced tool choice is verified in unit tests with a fake provider only. Confirm it
against the configured model before relying on drafts.

## Check health

| Endpoint | Authentication | Result |
|---|---|---|
| `GET /healthz` | Public | `{"status":"ok"}` |
| `GET /readyz` | Service token | `{"status":"ready"}` or HTTP 503 `{"status":"unavailable"}` |

Readiness checks configuration and database access within a deadline; no paid
model call or remote credit check.

## Request examples

### PowerShell

Set the token in this terminal. `uv --env-file` sets it only in the launched process.

```powershell
$env:AGENT_API_TOKEN = '<same service token as agent/.env>'
$agentPayload = '{"conversation_id":"11111111-1111-4111-8111-111111111111","message":"Explain the service scope."}'
$agentPayloadPath = Join-Path $env:TEMP 'denso-agent-request.json'
[System.IO.File]::WriteAllText($agentPayloadPath, $agentPayload)
curl.exe -N 'http://127.0.0.1:8010/v1/agent/stream' -H "Authorization: Bearer $env:AGENT_API_TOKEN" -H 'Content-Type: application/json' --data-binary "@$agentPayloadPath"
Remove-Item -LiteralPath $agentPayloadPath
```

Buffered response:

```powershell
Invoke-WebRequest -Uri 'http://127.0.0.1:8010/v1/agent/stream' -Method Post -Headers @{Authorization = "Bearer $env:AGENT_API_TOKEN"} -ContentType 'application/json' -Body $agentPayload
```

### POSIX shell

Save the same JSON payload as `request.json`:

```sh
curl -N http://127.0.0.1:8010/v1/agent/stream \
  -H "Authorization: Bearer $AGENT_API_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @request.json
```
