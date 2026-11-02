# Development and test findings

Status: planning evidence. No application changes were made.

## Selected development approach

Use one installed command, `denso-agent`, with `serve` and `setup-db` subcommands. Python's standard `argparse` module is sufficient. Both the command and the container load `denso_agent.app:create_app`. A factory is a function that creates the application. Model and database connections open during application startup, not during module import or argument parsing.

| Finding | Consequence | Source |
|---|---|---|
| Uvicorn accepts an import string, application factories and automatic reload. Reload and multiple workers are separate deployment choices. | Development uses the factory with reload; the container uses the same factory without reload and with one worker. | [Uvicorn settings](https://uvicorn.dev/settings/) |
| A console command declared through `[project.scripts]` requires an installed project with a build system. uv installs the project in editable mode by default. | Declare a build system and source package. Keep an independent lockfile. Source changes need a process reload, not a container build. | [uv project configuration](https://docs.astral.sh/uv/concepts/projects/config/) |
| Compose can wait for database health and for a setup job to finish successfully. | Agent startup depends on healthy PostgreSQL and completed `agent-setup`. | [Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/) |
| pytest supports registered test markers and expression-based selection. | Register suite and paid-smoke markers. Make live provider calls opt-in. | [pytest markers](https://docs.pytest.org/en/stable/example/markers.html) |

## CLI contract

The following examples describe planned commands. They are not executable until implementation exists. Run them from `D:/HACKATHONS/DENSO/denso-hackathon/`.

```text
uv sync --project agent --frozen --group dev
docker compose -f deployments/docker-compose.yml up -d --wait postgres
uv run --project agent --env-file agent/.env.setup denso-agent setup-db
uv run --project agent --env-file agent/.env denso-agent serve --reload
```

- `uv sync` installs the locked independent package and development dependencies.
- The Compose command starts only PostgreSQL and waits for readiness. It requires a Compose version supporting `--wait`.
- `setup-db` changes database structure. It exits successfully after applying missing setup steps.
- `serve --reload` starts the service on `127.0.0.1:8010`. Python source changes restart its serving process. PostgreSQL remains running.
- `--project agent` selects this package rather than the root backend project. `--env-file` selects the environment file for that process.
- Runtime `.env` and setup `.env.setup` are separate ignored files. Setup credentials must not enter the runtime process.
- Ordinary source edits require no dependency installation. Dependency or build-configuration changes require updating the lockfile and running sync again.

Illustrative package declaration:

```toml
[project.scripts]
denso-agent = "denso_agent.cli:main"

# Add a tested build system that installs src/denso_agent.
```

Illustrative command implementation, not final production code:

```python
def serve(args: Namespace) -> None:
    source_dir = Path(__file__).resolve().parent.parent
    uvicorn.run(
        "denso_agent.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(source_dir)] if args.reload else None,
        workers=1,
    )
```

`source_dir` resolves to the installed editable `agent/src` directory. Do not watch the repository root, `.venv`, test data or generated output. A console command calls `main()` only during command execution. Importing the module must not start a server. Help commands must not require credentials.

## Configuration boundaries

| Mode | Database address | Required secrets |
|---|---|---|
| Native CLI runtime | `localhost:5434/agent_memory` | Agent service token, ModelArk key, runtime database credentials |
| Container runtime | `postgres:5432/agent_memory` | Same runtime secrets, passed explicitly |
| Native CLI setup | `localhost:5434` | Bootstrap and setup-owner database credentials only |
| Container setup | `postgres:5432` | Bootstrap and setup-owner database credentials only |

The addresses reflect the current Compose port mapping. Changed `POSTGRES_PORT` values require matching local connection strings. Do not silently rewrite arbitrary database URLs. Separate settings classes prevent setup from demanding runtime secrets and prevent runtime from requiring administrative configuration. Using uv's `--env-file` loads all entries into the command environment; separate files are therefore necessary.

Compose may read a shared interpolation file, but the runtime service must receive only explicitly declared runtime variables. Passing the complete setup file through Compose `env_file` would expose administrative secrets to the agent. Existing backend APIs, workers and speech services must remain unnecessary for either agent mode.

## Conventional test additions

- `tests/unit/test_cli.py`: argument defaults, help without credentials, invalid commands, settings selection and no import-time resources.
- `tests/integration/test_cli_service.py`: launch the installed command as a child process against temporary PostgreSQL and a controlled model server. Confirm readiness, authenticated calls and persisted history across process restart.
- `tests/e2e/test_reload.py`: launch reload mode from a temporary project copy. Change a watched Python file in that copy. Observe serving-process replacement and successful requests afterwards. Confirm the previous conversation remains available.
- `tests/e2e/test_container_service.py`: launch the real image and disposable PostgreSQL/provider containers. Verify real network streaming, restart memory, setup rerun and shutdown cleanup.
- Root test configuration registers `unit`, `integration`, `e2e` and `live` markers. Default collection excludes paid smoke tests. Docker absence must fail selected integration/container suites clearly, not silently skip them.

Use real network clients for streaming checks. An in-process HTTP test client can collect an entire response and conceal buffering. The controlled model server emits one chunk, waits for an explicit test signal, then emits the remainder. Receiving the first chunk before releasing that signal proves streaming without a timing assertion.

Use a test-owned socket or bounded bind retry rather than assuming a free port remains available. Start child processes without a visible window on Windows. Readiness polling must have a deadline. Cleanup terminates the full reload process tree and releases the port. Process and container cleanup belongs in fixture `finally` blocks. Assertions must verify resource cleanup, not only response JSON.

## Planned file changes

- Add `agent/src/denso_agent/cli.py`; retain `setup_database.py` as the setup operation called by the CLI.
- Keep `RuntimeSettings` and `SetupSettings` in `config.py` while small. Split only if their responsibilities outgrow a readable module.
- Add build metadata and command entry point to `agent/pyproject.toml`; commit `agent/uv.lock` with a narrow root ignore exception.
- Add runtime and setup example env files. Ignore local env files, including `.env.setup`; the current root ignore names only `.env` and does not protect `.env.setup` automatically.
- Add `deployments/agent.compose.yml`; reuse the existing `postgres` service and volume. Inspect merged environment mappings and selected services.
- Document CLI-first development in `agent/README.md` and configuration differences in `agent/docs/development.md`.
- Add a new `.github/workflows/agent.yml` if repository automation is desired. No existing `.github/workflows` directory was found; no inherited workflow assumptions apply.

## Risks to retain in the plan

- Uvicorn reload restarts the process and can interrupt an active answer. It is a development convenience, not transparent request recovery.
- Process-local conversation locks require one serving worker and one agent instance. Do not run native and container agents against the same conversations concurrently.
- Docker overlay commands must retain the existing Compose project name to reuse the same database. Test projects need unique names and disposable volumes.
- Never use `docker compose down -v` on the developer stack to reset agent tests. It removes the shared database volume.
- The current backend dependency file is large. Reusing its environment would defeat independent development and slow container builds.
- Rules from the separate Hex project provide code principles, not commands or test-count claims for this repository.
