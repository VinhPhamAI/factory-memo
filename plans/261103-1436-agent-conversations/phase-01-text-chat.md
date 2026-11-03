# Phase 1 — Text chat and history

**Outcome.** An authenticated employee creates a chat, receives streamed text, and reopens saved history after an agent restart.

## Proposed changes

- Extend `agent/src/denso_agent/api/routes.py`, `schemas/chat.py`, and `runtime/conversation.py` with service-authenticated `GET /v1/agent/conversations/{id}/history`. Use the canonical conversation UUID as LangGraph thread ID and read `aget_state` asynchronously. Return ordered public user/assistant records and an explicit truncation indicator if the response limit is reached. A new thread with no checkpoint returns an empty list. Surface storage errors as errors. A saved user message can survive a failed turn. Partial assistant text stays provisional.
- Add `backend/api/conversations.py` and register it in `backend/app.py`. Use `GET/POST /v1/conversations`, `PATCH/DELETE /v1/conversations/{id}`, and `GET .../{id}/history` plus `POST .../{id}/stream`. Define request, response, limits and errors in the API docs before wiring callers. Use `require_current_user` from `backend/api/auth.py` on every route and derive owner ID from its returned account. Reuse the app database session wiring. Use injected dependencies for isolated tests and real bearer credentials in the integration smoke test.
- Add focused queries in `backend/src/storage/conversations.py` against the existing ORM model in `orm/inquiries.py`. Scope every query by authenticated owner and active record. Return 404 for an inaccessible chat ID. Soft-delete the row so submitted inquiry references remain valid. Keep database sessions short and release them before awaiting the agent.
- Add the backend HTTP client at `backend/src/clients/agent.py`. Reuse a lifespan-managed async client. Forward agent SSE events without buffering and close the upstream stream on downstream disconnect. Preserve upstream 409/429 before streaming starts. Never send the agent token to the browser.
- Wire a new `frontend/src/services/conversations-api.ts` to `frontend/src/hooks/workspace.tsx`. Use backend chat IDs, abort stale streams when switching chats, and replace provisional text with `complete.answer`. An EOF without a terminal event leaves completion unconfirmed. Reopen history to see saved messages. Keep resending an explicit user action.

## File-level implementation order

| Action | Path | Proposed symbols |
|---|---|---|
| Edit | `agent/src/denso_agent/schemas/chat.py` | `HistoryMessage` with role and text, and `ChatHistory` with messages and truncated |
| Edit | `agent/src/denso_agent/runtime/conversation.py` | `AgentRuntime.load_history` reads the checkpoint, projects public messages, then takes the newest messages within the response budget in chronological order |
| Edit | `agent/src/denso_agent/api/routes.py` | `history` validates the UUID, reuses service authentication, then delegates to the runtime |
| Create | `backend/src/storage/conversations.py` | `create_conversation`, `list_owned_conversations`, `require_active_owner`, `rename_owned_conversation`, `soft_delete_owned_conversation` |
| Create | `backend/src/clients/agent.py` | `AgentClient.fetch_history` and `AgentClient.open_stream`; attach the service credential and translate upstream failures |
| Create / register | `backend/api/conversations.py`, `backend/app.py` | Conversation routes and lifespan-managed HTTP client |
| Create / edit | `frontend/src/services/conversations-api.ts`, `frontend/src/hooks/workspace.tsx` | Typed CRUD/history calls, SSE decoding, and selection/send state keyed by conversation ID |

Call sequence: browser → backend authentication → active-owner query → agent HTTP API → LangGraph checkpoint. A stream follows the same authorization sequence. It then sends the current user input to the graph and forwards events to the browser.

Use the existing `useAuthClient` and in-memory access token for browser requests.
Handle auth expiry before opening the stream and surface a rejected stream
without automatically sending the chat message again.

Show a truncation notice above the returned messages. A trailing saved user message is displayed as a user turn with no saved answer. Show an interrupted status in the sending UI when the stream ends without completion, and reconcile displayed messages from history on reopen.

**First failing tests.** Extend `agent/tests/integration/test_memory.py` to call the proposed history endpoint after restarting against the same database. Create `backend/tests/test_conversations.py` with a second-user request that asserts the fake agent client received zero calls. Add `frontend/src/services/conversations-api.test.ts` for the completion and EOF event sequences.

## Boundary sketch

```python
async def load_history(conversation_id: UUID, owner_id: UUID) -> ChatHistory:
    """Check access before requesting the agent's public message history."""
    await conversations.require_active_owner(conversation_id, owner_id)
    return await agent_client.fetch_history(conversation_id)
```

The browser identity comes from authentication. The agent client attaches its own service credential. The backend storage module queries conversation rows only.

## Checks

- [ ] Test CRUD and history/stream access for owner, second user, and deleted chat. Verify deletion preserves an existing inquiry reference.
- [ ] Test agent service authentication, empty history, ordered messages, hidden internal messages, bounded output, and unavailable storage.
- [ ] Use disposable PostgreSQL and a controlled provider to reopen after restart. Exercise failed generation or save, including the possibility of retained user input.
- [ ] Use real HTTP to prove incremental delivery and cleanup on disconnect. Test two conversations stream concurrently and a second request on the same conversation returns 409.
- [ ] Test chat switching, final answer replacement, and EOF/error handling in the frontend.
