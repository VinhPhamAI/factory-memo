# Standalone agent

Streams ModelArk answers and saves conversation history in PostgreSQL. Supports
standalone chat and optional knowledge-base search through the backend. For the
signed-in UI and KB tools, follow [local chat setup](../docs/features/conversations.md#run-locally).

[API and request examples](docs/api.md) · [Settings and development](docs/development.md)

## Requirements

- Python 3.12+ and `uv` (tested with 0.7.13).
- Docker with Compose 2.24+ for PostgreSQL and container tests.
- ModelArk API key, base URL and an activated model or endpoint ID.

Run all commands from the repository root.

## Local setup

### 1. Prepare configuration

For a new setup only. Keep existing local files.

```powershell
Copy-Item agent/.env.example agent/.env
Copy-Item agent/.env.setup.example agent/.env.setup
```

| File | Set |
|---|---|
| `agent/.env` | Service token, ModelArk settings and runtime database URLs |
| `agent/.env.setup` | Bootstrap credentials and owner/runtime database URLs |

- Replace placeholder secrets. Both files are ignored by Git.
- Match the owner password across its local/container URLs, and the runtime
  password across all three runtime URLs in both files.
- URL-encode special characters in database usernames and passwords.
- Bootstrap credentials must allow database/role creation; defaults match Compose.

### 2. Install and prepare PostgreSQL

```powershell
uv sync --project agent --frozen --group dev
docker compose -f deployments/docker-compose.yml up -d --wait postgres
uv run --project agent --env-file agent/.env.setup denso-agent setup-db
```

- Creates `agent_memory` and checkpoint tables on the shared PostgreSQL server.
- Reruns preserve conversations on existing volumes. Run one setup at a time.
- Owner credentials apply setup; restricted runtime credentials serve requests.
  Serving never runs migrations.

### 3. Start the agent

```powershell
uv run --project agent --env-file agent/.env denso-agent serve --reload
```

Default address: **http://127.0.0.1:8010**.

- Edits under `agent/src` restart the server without rebuilding an image.
- Reload can interrupt active answers.
- Dependency changes require a reviewed lock update and another sync.

Help requires no credentials:

```text
uv run --project agent denso-agent --help
uv run --project agent denso-agent serve --help
uv run --project agent denso-agent setup-db --help
```

## Docker

Stop the local agent first. Use the same configuration files:

```text
docker compose --env-file agent/.env --env-file agent/.env.setup -f deployments/docker-compose.yml -f deployments/agent.compose.yml up --build agent
```

- Starts PostgreSQL, setup and agent. Does not start the backend, worker or speech.
- Runtime gets only listed settings. Admin credentials stay in the setup job.
- Image runs as a nonroot user; its published port is accessible only from this computer.
- Across CLI and Docker, run **one serving instance and one worker**; request limits apply within that process.

| Address | Local CLI | Container |
|---|---|---|
| PostgreSQL | `localhost:5434` | `postgres:5432` |
| Agent port | `--port` (default 8010) | `AGENT_PORT` (default 8010) |

Update local database URLs if PostgreSQL's published port changes.

## API essentials

- Send `Authorization: Bearer <AGENT_API_TOKEN>`; keep it out of browser code.
- The service token and ModelArk key are separate credentials.
- The backend checks user login and conversation ownership.
- Append `text.delta` for progress. **Replace** it with the full saved
  `complete.answer` when complete; strict validators must accept `answer`.
- No `complete` means unfinished. A disconnect can also lose the final event.
- To enable knowledge-base tools, set `AGENT_KB_TOOLS_ENABLED=true` in the backend
  environment. For native serving, set `AGENT_KB_BACKEND_URL=http://127.0.0.1:8005`
  in `agent/.env`. For paired Compose services, `AGENT_CONTAINER_KB_BACKEND_URL`
  defaults to `http://api:8005`. Set `API_AGENT_BASE_URL=http://agent:8010` in the
  backend environment when its agent also runs in Compose.
  The agent uses the authenticated employee's access token
  for KB calls. See [conversation settings](../docs/features/conversations.md#settings).

| Endpoint | Access | Purpose |
|---|---|---|
| `/healthz` | Public | Process health; no model call |
| `/readyz` | Service token | Database readiness; no model call |
| `/docs`, `/openapi.json` | Public | Request schemas |

## Tests and checks

```text
uv run --project agent pytest -c agent/pyproject.toml agent/tests/unit -q
uv run --project agent pytest -c agent/pyproject.toml agent/tests/integration -q
uv run --project agent pytest -c agent/pyproject.toml agent/tests/e2e -q
uv run --project agent pytest -c agent/pyproject.toml agent/tests --collect-only -q
uv run --project agent black --check agent/src agent/tests
uv run --project agent pylint --rcfile agent/pyproject.toml agent/src/denso_agent
```

| Suite | Uses |
|---|---|
| Unit | No Docker, network or real keys |
| Integration | Disposable PostgreSQL 17 and a controlled model server |
| End-to-end | Installed CLI and actual image |

- Selected infrastructure suites fail if Docker is unavailable.
- Cleanup removes only uniquely named test resources.
- Default tests make no paid calls. Paid tests require **both** `-m live` and
  `AGENT_RUN_LIVE=1`; normal verification uses controlled responses.
