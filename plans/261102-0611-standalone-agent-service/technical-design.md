# Technical design

Status: implemented and verified. Code sketches below remain illustrative; the installed package and service documentation are authoritative. The committed lock and final tests verify the actual stream, checkpoint and SDK signatures. See [implementation verification](reports/implementation-verification.md).

A checkpoint is saved graph execution state. A schema declares allowed data fields. A fixture supplies controlled test data or resources. A semaphore bounds concurrent requests. Idempotency means repeated requests do not repeat their effects; this API does not provide it. SDK means software development kit; the OpenAI Python client is the provider SDK used here.

## Package boundaries

```text
agent/
  pyproject.toml, uv.lock, Dockerfile, .dockerignore, README.md
  .env.example, .env.setup.example
  docs/{api.md,development.md}
  src/denso_agent/
    __init__.py, cli.py, app.py, config.py, setup_database.py
    event_loop.py, windows_reload.py
    api/{routes.py,authentication.py,sse.py,body_limit.py,stream_response.py}
    schemas/{chat.py,events.py}
    runtime/{graph.py,conversation.py,errors.py}
    clients/{modelark.py,checkpoints.py}
  tests/{conftest.py,unit/,integration/,e2e/}
deployments/agent.compose.yml
.github/workflows/agent.yml
```

Add package initialization files where required. Group code by responsibility. Review files approaching 300 lines without arbitrary splitting. Handlers authenticate, validate and delegate; they contain no database queries. Runtime calls clients. Data shapes live in `schemas/`. No `backend.*` imports are allowed. Existing backend code supplies ideas only.

New or modified functions and classes require short, purpose-specific docstrings. The opening sentence explains the operation and, where useful, how it transforms input into output. Include `Args:`, `Returns:` and `Raises:` only where relevant. Describe each argument's meaning, the returned result and the condition for each documented exception. Omit empty sections, generic filler and signature restatements. A docstring must explain its subject without relying on unstated conversation context.

Use Python 3.12+, FastAPI, Uvicorn, Pydantic Settings, LangGraph, langchain-core message types, langgraph-checkpoint-postgres, psycopg binary/pool extras and the OpenAI Python SDK. Use direct `AsyncOpenAI`, not `ChatOpenAI` or `langchain-openai`. The adapter's token-field rewriting is unnecessary for this version. Development dependencies include pytest, pytest-asyncio, httpx, Black and Pylint. Resolve compatible concrete versions and commit `agent/uv.lock`; use frozen installs in development and builds. Formatting/lint gates use actual exit status without borrowed scores or test-count floors.

Illustrative packaging fragment; dependency pins, metadata and pytest configuration are omitted:

```toml
[build-system]
requires = ["hatchling"] # Lock a tested version during implementation.
build-backend = "hatchling.build"
[project.scripts]
denso-agent = "denso_agent.cli:main"
[tool.hatch.build.targets.wheel]
packages = ["src/denso_agent"]
```

Modify root `.gitignore` only for `!agent/uv.lock` and ignored local setup secrets such as `agent/.env.setup`. Retain existing `.env` protection. Commit example files with placeholders only.

## Native command and settings

Use standard-library `argparse` with two subcommands. `denso-agent serve` defaults to `127.0.0.1:8010`; `--reload` enables source reload. `denso-agent setup-db` performs provisioning/migrations and exits. All help variants work without credentials or network access. Imports and argument parsing open no provider/database resources.

Use `RuntimeSettings` and `SetupSettings` in `config.py`. Load separate ignored `agent/.env` and `agent/.env.setup` files. Setup settings must not require a ModelArk key, service token or runtime configuration. Runtime settings must not require administrative credentials. Never pass an entire setup environment file to the runtime container.

| Runtime variable | Selected policy |
|---|---|
| `AGENT_API_TOKEN` | Required nonempty trusted-service secret |
| `MODELARK_API_KEY`, `MODELARK_BASE_URL`, `MODELARK_MODEL` | Explicit required provider configuration |
| `AGENT_DATABASE_URL` | Restricted role connected to `agent_memory` |
| `AGENT_REQUEST_TIMEOUT_SECONDS` | Initial default 120; positive validated bound |
| `AGENT_MAX_OUTPUT_TOKENS` | Initial default 1024; model compatibility requires verification |
| `AGENT_MAX_CONCURRENT_REQUESTS` | Initial default 4; positive bounded value |
| `AGENT_CONTEXT_MAX_MESSAGES` | Initial default 40 |
| `AGENT_CONTEXT_MAX_CHARACTERS` | Initial default 60000 |

Setup variables are `AGENT_BOOTSTRAP_DATABASE_URL`, `AGENT_SETUP_DATABASE_URL` and the intended `AGENT_DATABASE_URL` for provisioning the restricted runtime login/password. The restricted runtime credential is repeated in the separate setup/runtime files; administrative values are never passed to runtime. Bootstrap credentials can create the isolated database/roles. Setup-owner credentials apply checkpoint migrations. Runtime credentials cannot create databases, roles or schema objects. All saver connections use native psycopg `postgresql://` connection strings, not the root backend's SQLAlchemy `postgresql+psycopg://` form.

Native database addresses use `localhost:5434`; container addresses use `postgres:5432`, reflecting current Compose ports. Changed host port mappings require explicit matching configuration. Switching mode must not require editing local files or rewriting arbitrary URLs.

Store the native URLs and their container equivalents together in the example files:

| File | Variables and use |
|---|---|
| `agent/.env` | Runtime secrets, native `AGENT_DATABASE_URL`, and `AGENT_CONTAINER_DATABASE_URL` for Compose interpolation only |
| `agent/.env.setup` | Native bootstrap/setup/runtime URLs for CLI provisioning, plus `AGENT_CONTAINER_BOOTSTRAP_DATABASE_URL` and `AGENT_CONTAINER_SETUP_DATABASE_URL` for Compose interpolation only |

Both forms use the same database name and role passwords. Only the connection address differs. Native CLI settings ignore the container-only variables. Compose maps the container variables explicitly; the runtime container receives none of the setup variables. The repeated native runtime URL in the setup file must match the runtime file, but Compose does not use that overwritten native value for either service.

Illustrative CLI function; parser, validation and command dispatch are omitted:

```python
def serve(args):
    source_dir = Path(__file__).resolve().parent.parent
    uvicorn.run(
        "denso_agent.app:create_app", factory=True,
        host=args.host, port=args.port, workers=1,
        reload=args.reload,
        reload_dirs=[str(source_dir)] if args.reload else None,
    )
```

The editable installation resolves `source_dir` to `agent/src`. Watch no repository root, environment directory or generated data. The container invokes `denso-agent serve --host 0.0.0.0` without reload. Reload interrupts active answers; it is not transparent recovery.

Planned commands from repository root, executable after implementation only:

```text
uv sync --project agent --frozen --group dev
docker compose -f deployments/docker-compose.yml up -d --wait postgres
uv run --project agent --env-file agent/.env.setup denso-agent setup-db
uv run --project agent --env-file agent/.env denso-agent serve --reload
```

`--project agent` selects the independent package. `--frozen` forbids implicit lock changes. `--group dev` includes test dependencies. `--env-file` appears before the installed command and selects that process's credentials. The Compose command starts only PostgreSQL and waits for health. Setup exits after applying missing database/schema steps. Serve listens on port 8010 and restarts its serving process after Python source edits. Ordinary source edits need no image rebuild or dependency install; dependency edits require a reviewed lock update and sync.

## API and trust boundary

`POST /v1/agent/stream` requires `Authorization: Bearer <AGENT_API_TOKEN>`. Compare tokens in one authentication function with a constant-time comparison. Reject missing/invalid tokens with 401 before model/checkpoint work. This secret identifies a trusted caller, not a logged-in user. The backend must authorize conversation ownership. All authorized callers share the same trust scope. Browsers must not receive this secret.

| Request field | Rule |
|---|---|
| `conversation_id` | Required UUID, a standard unique identifier; chosen by the trusted caller |
| `message` | Required string; trim boundaries; length 1..20000 characters |

Reject unknown fields. Cap the HTTP body at 128 KiB before JSON parsing, including chunked requests. Generate `request_id` on the server for tracing only; it prevents no duplicates. Manual resend creates a fresh turn and can repeat context. Disable generation retries.

Illustrative valid JSON:

```json
{"conversation_id":"11111111-1111-4111-8111-111111111111","message":"Explain the service scope."}
```

`GET /healthz` is public and returns only `{"status":"ok"}`. `GET /readyz` requires the token and returns `{"status":"ready"}` or 503 `{"status":"unavailable"}` after a bounded database/configuration check. Neither endpoint calls the paid model or reveals connection/provider details.

Before opening SSE, atomically reject a second active request for one conversation with 409. Reject global capacity exhaustion with 429. Keep only active identifiers and a fixed-capacity semaphore; release both in `finally`. No indefinitely growing conversation-lock map is allowed. Run one serving worker and one active instance. PostgreSQL persistence does not make these process-local controls distributed.

## SSE contract

Set `Content-Type: text/event-stream`, disable caching and proxy buffering, and send UTF-8 frames. Each event data object contains `request_id`, `conversation_id` and increasing `sequence`, beginning at 1. Declare typed event schemas independently of LangGraph's internal stream format.

| Event | Additional body | Meaning |
|---|---|---|
| `status` | `state: "generating"` | Execution admitted |
| `text` | `delta: string` | Standard answer text fragment |
| `complete` | `finish_reason: "stop"` | Successful graph exhaustion and final checkpoint save |
| `error` | `code`, `message`, `retryable` | Sanitized failure without successful completion |

Illustrative frames; UUIDs are mock identifiers:

```text
event: status
data: {"request_id":"22222222-2222-4222-8222-222222222222","conversation_id":"11111111-1111-4111-8111-111111111111","sequence":1,"state":"generating"}

event: text
data: {"request_id":"22222222-2222-4222-8222-222222222222","conversation_id":"11111111-1111-4111-8111-111111111111","sequence":2,"delta":"This service maintains conversations."}

event: complete
data: {"request_id":"22222222-2222-4222-8222-222222222222","conversation_id":"11111111-1111-4111-8111-111111111111","sequence":3,"finish_reason":"stop"}

```

Exactly one terminal event (`complete` or `error`) is generated for a connected request. Delivery after disconnect cannot be guaranteed. Previously delivered text remains provisional until `complete`. No private reasoning, provider errors, credentials or raw internal metadata appear in public output.

Before streaming, use JSON `{"error":{"code":"unauthorized","message":"Authentication required."}}`. Normalize validation failures into the same envelope. Selected HTTP codes: 401 unauthorized, 413 payload_too_large, 422 invalid_request, 409 conversation_busy, 429 capacity_exceeded and 503 unavailable. Include the server request identifier when assigned. After streaming starts, HTTP status remains 200; report typed SSE errors.

Selected stream error codes: timeout, provider_error, context_limit, output_limit, filtered_output, empty_output and persistence_error. Unexpected finish reasons are provider_error. `retryable` describes whether a fresh manual request may be attempted; it does not promise safe duplicate-free replay. Empty answers, length-limited answers and filtered answers never produce `complete`.

## Graph and provider execution

Use `StateGraph(MessagesState)` with START -> answer -> END. The node uses the direct SDK stream. Configure explicit base URL/key, `max_retries=0`, timeout and model. Send Chat Completions requests with `stream=True` and `max_tokens`; verify exact outgoing fields with a real HTTP stub. Read standard `delta.content` only. Ignore private reasoning extension fields.

The prompt states that this service has no source evidence or tools. It must not invent factory records, photographs, inspections or verified repairs. Add the system instruction once to model input on each generation, not repeatedly to persisted history. Select recent context within both 40-message and 60000-character defaults. Keep a coherent recent suffix and the current message. Reject a current message that cannot fit configured input bounds. Do not mutate full persisted history. These bounds do not guarantee an unknown model's exact token count; report provider context-limit failures clearly. A validated model-specific estimator is optional.

Illustrative node and graph; message conversion, typed errors, context selection and output bounds are named helpers omitted for clarity:

```python
async def answer(state: MessagesState):
    writer = get_stream_writer()
    parts, finish_reason = [], None
    messages = provider_messages(select_context(state["messages"]))
    stream = await client.chat.completions.create(
        model=settings.model, messages=messages,
        stream=True, max_tokens=settings.max_output_tokens,
    )
    try:
        async for chunk in stream:
            for choice in chunk.choices:
                if choice.delta.content:
                    check_answer_bound(parts, choice.delta.content)
                    parts.append(choice.delta.content)
                    writer({"delta": choice.delta.content})
                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason
    finally:
        await stream.close()
    validate_finished_answer(finish_reason, parts)  # Nonempty + stop only.
    return {"messages": [AIMessage(content="".join(parts))]}

builder = StateGraph(MessagesState)
builder.add_node("answer", answer)
builder.add_edge(START, "answer")
builder.add_edge("answer", END)
graph = builder.compile(checkpointer=saver)
```

Define `MAX_ANSWER_CHARACTERS = 32_000` beside the answer-buffer check in `clients/modelark.py`. Count characters using Python `len`, with a running total across fragments. Check before appending or emitting the next fragment. Exactly 32000 characters may succeed; exceeding the cap produces `output_limit`, closes upstream and prevents completion. This fixed buffer cap is separate from the requested provider token budget. Missing finish reason is a provider error. A length stop is incomplete output even if text was emitted. The node returns an assistant message only after a nonempty successful stop.

Illustrative stream consumption; public schema creation, disconnect detection and sanitized error mapping are omitted:

```python
async for part in graph.astream(
    {"messages": [HumanMessage(content=message)]},
    config={"configurable": {"thread_id": str(conversation_id)}},
    stream_mode="custom", version="v2", durability="sync",
):
    yield encode_text_event(part["data"]["delta"])
# Exhaustion must succeed, including checkpoint writes, before this point.
yield encode_complete_event(finish_reason="stop")
```

The internal custom-stream format is not a public contract. Confirm v2/durability signatures with locked versions. If final persistence fails, emit error instead of complete. Fresh questions provide fresh input; do not call graph execution with `None` to resume a failed question.

## Resource lifetime and incomplete turns

`create_app` creates the application without opening connections. Its lifespan opens one provider client and PostgreSQL pool/saver, compiles the graph and closes resources on shutdown or failed startup. Request execution closes upstream streams and releases admission state on completion, cancellation, disconnect and error. Cancellation of paid provider execution is best effort.

Illustrative resource ownership; concrete pool configuration, readiness and injection are omitted:

```python
@asynccontextmanager
async def lifespan(app):
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(AsyncOpenAI(
            base_url=settings.base_url, api_key=settings.api_key,
            max_retries=0, timeout=settings.request_timeout,
        ))
        pool = await stack.enter_async_context(open_runtime_pool(settings))
        app.state.runtime = build_runtime(client, pool)
        yield
```

Use AsyncPostgresSaver as the only conversation-history store. Add no operational request table or parallel chat table. The saver may preserve human input/pending work before failure. The node never returns a partial assistant message as successful state. A database failure may leave an unacknowledged full result; rollback is not guaranteed. Real saver tests must inspect stored state and establish fresh-input behavior after provider error, cancellation and final-write failure. Manual resend can repeat a human turn. No exactly-once execution, automatic retry, stream replay or crash-resume guarantee is provided.

Generation context is bounded; checkpoint storage is not automatically pruned. Document growth and explicit isolated-database maintenance. Never remove the shared backend volume as a reset. Long-term retention is a separate future decision.

## Database setup and container path

Reuse current PostgreSQL 17 in its current Compose project. Create logical database `agent_memory` owned by a setup role; grant a separate runtime role CONNECT, schema USAGE and required table/sequence operations only. Test actual saver access under this role. Set default grants for future setup-owner-created tables. Runtime neither provisions nor migrates schema.

The one-off setup operation uses bootstrap credentials to create missing roles/database on existing volumes, then setup-owner credentials to await `AsyncPostgresSaver.setup()`. Database creation uses autocommit and fixed validated identifiers. Apply required grants after migrations. Reruns preserve data. Do not rely only on `docker-entrypoint-initdb.d`, which does not run on an existing volume.

Upgrade sequence: back up `agent_memory`, stop the active agent instance, update the reviewed lock/image, run setup once, verify readiness/compatibility, then start the agent. Incompatible migration rollback uses the backup. Only one setup process runs at a time.

Illustrative Compose overlay; provider settings, other runtime limits, build details and health-check command are omitted. The database mappings below are required. Setup credentials belong only to `agent-setup`:

```yaml
services:
  agent-setup:
    build: {context: ../agent}
    command: ["denso-agent", "setup-db"]
    environment:
      AGENT_BOOTSTRAP_DATABASE_URL: ${AGENT_CONTAINER_BOOTSTRAP_DATABASE_URL}
      AGENT_SETUP_DATABASE_URL: ${AGENT_CONTAINER_SETUP_DATABASE_URL}
      AGENT_DATABASE_URL: ${AGENT_CONTAINER_DATABASE_URL}
    depends_on:
      postgres: {condition: service_healthy}
    restart: "no"
  agent:
    build: {context: ../agent}
    command: ["denso-agent", "serve", "--host", "0.0.0.0"]
    environment:
      AGENT_DATABASE_URL: ${AGENT_CONTAINER_DATABASE_URL}
    ports: ["127.0.0.1:8010:8010"]
    depends_on:
      postgres: {condition: service_healthy}
      agent-setup: {condition: service_completed_successfully}
```

Use `deployments/agent.compose.yml` with the existing Compose file and project name. Docker Compose resolves relative paths against the first file; `../agent` above is relative to `deployments/`. Runtime receives explicit runtime variables only. The planned interpolation command is `docker compose --env-file agent/.env --env-file agent/.env.setup -f deployments/docker-compose.yml -f deployments/agent.compose.yml up --build agent`. Both files supply interpolation values, but explicit per-service mappings exclude setup/admin values from runtime. The named container URLs permit unchanged env files when switching from a stopped native agent to the image. Starting target `agent` must not start backend API, worker or speech or require their secrets.

Build the independent package with a frozen lock and a tested Python base image. Add `agent/.dockerignore` excluding local runtime/setup env files, virtual environments and caches; keep safe example files. Run as nonroot. Use the same factory as native mode, without reload. CI runs a new `.github/workflows/agent.yml` for this package with deterministic provider fixtures and disposable PostgreSQL. Test projects have unique names/volumes and never reuse developer data.

## Documentation and request examples

Create `agent/README.md` for prerequisites, separate env files, native setup/run, container mode and tests. Create `agent/docs/api.md` for exact field/event/error tables and caller trust. Create `agent/docs/development.md` for folder responsibilities, red-green-refactor, reload, checkpoints, upgrade and failure behavior. Generated OpenAPI derives from schemas; SSE documentation supplements it. Use impersonal professional English and literal short sentences.

Planned PowerShell example; `AGENT_API_TOKEN` is loaded in the terminal and the payload file is temporary. `Invoke-WebRequest` may buffer; use `curl.exe -N` to inspect streaming:

```powershell
$agentPayload = '{"conversation_id":"11111111-1111-4111-8111-111111111111","message":"Explain the service scope."}'
$agentPayloadPath = Join-Path $env:TEMP 'denso-agent-request.json'
[System.IO.File]::WriteAllText($agentPayloadPath, $agentPayload)
curl.exe -N 'http://127.0.0.1:8010/v1/agent/stream' -H "Authorization: Bearer $env:AGENT_API_TOKEN" -H 'Content-Type: application/json' --data-binary "@$agentPayloadPath"
```

POSIX curl uses the same JSON file with `curl -N ... -H "Authorization: Bearer $AGENT_API_TOKEN" -H 'Content-Type: application/json' --data-binary @request.json`. Include a PowerShell `Invoke-WebRequest -Body $agentPayload` example for non-stream inspection. Exercise documented examples against the deterministic test provider; do not present them as already executed.

## Primary references

[LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming), [durability](https://docs.langchain.com/oss/python/langgraph/checkpointers#durability-modes), [memory trimming](https://docs.langchain.com/oss/python/langgraph/add-memory#trim-messages), [PostgreSQL saver source](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py), [ModelArk authentication](https://docs.byteplus.com/en/docs/modelark/base-url-and-authentication?redirect=1), [ModelArk streaming](https://docs.byteplus.com/en/docs/modelark/streaming-output?redirect=1), [Uvicorn settings](https://uvicorn.dev/settings/), [uv packaging](https://docs.astral.sh/uv/concepts/projects/config/), [Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/).
