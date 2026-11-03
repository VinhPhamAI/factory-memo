# Development notes

## Find the code

| Location | Responsibility |
|---|---|
| `cli.py` | Commands and dispatch; credential-free help opens no resources. |
| `config.py` | Validate separate runtime and setup settings. |
| `event_loop.py` | PostgreSQL-compatible Windows/Unix event loops. |
| `windows_reload.py` | Stop and replace Windows workers within fixed time limits. |
| `app.py` | FastAPI factory; provider/database startup and shutdown. |
| `api/` | Authentication, validation, request limits, public events. |
| `schemas/` | Define request, error, and event fields. |
| `runtime/` | One-node graph, recent context, request limits. |
| `clients/modelark.py` | Call ModelArk through `AsyncOpenAI`; close streams; private tool routing. |
| `runtime/retrieval.py` | Search tool schema, trusted scope, bounded evidence, citation validation. |
| `clients/checkpoints.py` | Own the runtime PostgreSQL pool and saver. |
| `setup_database.py` | Create database/roles, update tables, assign permissions. |

**Change rules**

- Define data fields, limit in-process state, and give resources clear owners.
- Check service credentials in one place. Keep database queries out of handlers.
- Import no existing backend modules.
- Use specific, concise docstrings with relevant `Args:`, `Returns:`, and `Raises:`; no empty sections or filler.
- Write a failing behavior test, implement, then refactor with tests passing.
- Select test directories directly; marker filters must not omit unmarked tests.
- Keep fake dependencies in tests; production has no fake-provider bypass.

## Configure the service

| File | Use | Required credentials |
|---|---|---|
| `agent/.env` | Serving | Service token, provider key, restricted database URL; no owner/admin credentials. |
| `agent/.env.setup` | Database setup | Bootstrap, owner, intended runtime URLs; no provider key or service token. |

Both files are ignored; examples contain placeholders.

### Runtime settings

| Setting | Purpose / default |
|---|---|
| `AGENT_API_TOKEN` | Required shared service secret. |
| `MODELARK_API_KEY` | Required provider key; separate from the service secret. |
| `MODELARK_BASE_URL` | Required provider base URL. |
| `MODELARK_MODEL` | Required model or endpoint ID. |
| `AGENT_DATABASE_URL` | Restricted PostgreSQL runtime URL. |
| `AGENT_REQUEST_TIMEOUT_SECONDS` | Entire request deadline: 120 seconds. |
| `AGENT_MAX_OUTPUT_TOKENS` | Requested provider output budget: 1024 tokens. |
| `AGENT_MAX_CONCURRENT_REQUESTS` | Global active requests: 4. |
| `AGENT_CONTEXT_MAX_MESSAGES` | Recent input messages: 40. |
| `AGENT_CONTEXT_MAX_CHARACTERS` | Recent input characters: 60000. |
| `AGENT_SEARCH_TIMEOUT_SECONDS` | Knowledge search deadline: 10 seconds. |
| `AGENT_SEARCH_MAX_RESULTS` | Evidence items given to the model: 5. |
| `AGENT_SEARCH_MAX_EXCERPT_CHARACTERS` | Characters per evidence excerpt: 1500. |
| `AGENT_HISTORY_MAX_MESSAGES` | Newest messages returned by history: 200. |
| `AGENT_HISTORY_MAX_CHARACTERS` | Characters returned by history: 400000. |

### Setup and container URLs

Setup URLs: `AGENT_BOOTSTRAP_DATABASE_URL`, `AGENT_SETUP_DATABASE_URL`,
`AGENT_DATABASE_URL` (runtime account to provision).

| Container-only variable | File |
|---|---|
| `AGENT_CONTAINER_DATABASE_URL` | `agent/.env` |
| `AGENT_CONTAINER_BOOTSTRAP_DATABASE_URL` | `agent/.env.setup` |
| `AGENT_CONTAINER_SETUP_DATABASE_URL` | `agent/.env.setup` |

- Use native `postgresql://` URLs, not `postgresql+psycopg://`.
- Compose explicitly maps container variables to application settings.
- Stop native serving before switching to Docker; keep the same configuration.

## Understand answer handling

A checkpoint saves conversation and graph execution state.

The runtime reads custom text and node updates from one graph stream:

1. Select recent context; preserve saved history.
2. Add the system instruction to input only. With a retrieval client and a request
   `search_scope`, a private routing call may request one `search_knowledge` query;
   the evidence and a matching instruction go to the answer call only
   (`runtime/retrieval.py`). A request photo is added to the current user message
   only (`TurnImage` in per-run configuration, bytes not text, so it is never saved).
3. Stream standard content as `text` and the provider's `reasoning_content` as
   `reasoning` events (never saved, never part of the answer). Create an assistant
   message only after nonempty `stop`.
4. Read the full message from the graph's answer-node update.
5. Finish the graph stream and synchronous checkpoint saving.
6. Send `complete.answer`. The client replaces provisional text with this value.

| Rule | Behavior |
|---|---|
| Answer buffer | 32000 characters across fragments, measured by Python `len`. Reject overflow before appending or emitting. |
| Final answer | One valid, nonempty assistant string; no reconstruction or extra database read. |
| Invalid graph output | Missing, malformed, ambiguous output → `persistence_error`; no completion. |
| Save acknowledgement | Node updates alone are insufficient; the graph stream must finish successfully. |
| Failure | Failed save, timeout, provider failure, cancellation → no successful completion. |
| Input limits | Character limits are not exact model token counts. |

### Conversation memory

- Only history store: `AsyncPostgresSaver`. Caller UUIDs become graph thread IDs.
- Saver setup manages versioned tables/migrations; do not copy them into backend migrations.
- Failed turns may retain human input/pending work; partial assistant fragments are not successful history.
- Database failure may leave a saved full answer without acknowledgement; no rollback guarantee.
- Use fresh human input after failure, never `None` resume. Manual resend can duplicate context.
- No automatic retry or replay. Manual requests may repeat effects; recovery after a crash is not guaranteed.
- Lost final event → unconfirmed completion; read the history endpoint to see what was saved. Provider cancellation is best effort.

## Run and reload safely

- One worker, one active instance; request tracking stays in that process.
- One active request per conversation; fixed global request limit.
- Close provider streams and release active IDs on completion, error, timeout, disconnect.
- Source reload can interrupt active answers.

| Platform | Reload behavior |
|---|---|
| Windows | Stop within fixed time limits before replacement; refuse a second worker if stopping fails. Locked Uvicorn interfaces; tested through the installed CLI. |
| Unix | Standard Uvicorn reload supervisor. |

## Maintain the database

| Item | Policy |
|---|---|
| Server | Shared PostgreSQL; preserve backend databases and the existing volume. |
| Agent database | Fixed name `agent_memory`. |
| Setup owner | Fixed role `agent_owner`; applies migrations. |
| Runtime | Fixed `agent_runtime` role: connection, schema usage, required table/sequence rights; no schema/role/database creation. |
| Connection targets | Same PostgreSQL server for all setup URLs; reject privileged or unrelated existing roles. |
| Retention | No automatic pruning; maintain the isolated database separately. |
| Resets | Never delete the shared developer volume to clear history. |

### Upgrade the saver

1. Back up `agent_memory` using PostgreSQL tools.
2. Stop the agent.
3. Review and update the independent lock and image.
4. Run one `setup-db` process.
5. Verify permissions, readiness, and checkpoint compatibility.
6. Start the agent. Restore the backup if migrations are incompatible.

## Verify changes

| Check | Evidence |
|---|---|
| Streaming | Real TCP client receives a fragment before the controlled provider releases the next; detects buffering. |
| Persistence | Real PostgreSQL 17; inspect checkpoints after failures. |
| Restarts | Native process and image retain completed conversations. |
| Reload | Edit a temporary package copy; preserve repository source. |
| Cleanup | Remove only uniquely named, test-owned resources. |
| Model calls | Normal tests use controlled HTTP responses, never a paid key. |

Paid tests require explicit opt-in; compatibility depends on account/model.
Implementation-plan reports record results and environment limits.
