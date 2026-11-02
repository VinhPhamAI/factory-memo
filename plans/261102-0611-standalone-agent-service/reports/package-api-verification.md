# Package and API verification

Date: 2026-11-02. Scope: independent package, CLI, configuration, HTTP contracts,
admission, response lifetime and application resource ownership.

## Implementation checks

- Installed the independent package using `uv sync --project agent --group dev`.
  The committed lock resolves LangGraph 1.2.19, checkpoint-postgres 3.2.0,
  OpenAI 2.54.0 and Python 3.12-compatible dependencies.
- The CLI exposes `serve` and `setup-db`. Help requires no configuration.
  Native reload watches only package source. Uvicorn uses one worker and the
  explicit event-loop factory needed by psycopg on Windows.
- The raw-body middleware checks declared and chunked bodies before JSON
  decoding. Length is checked before copying an incoming chunk into the buffer.
  Very long numeric Content-Length values do not require integer conversion.
- One authentication function compares UTF-8 token bytes in constant time.
  Request schemas reject unknown fields and normalize whitespace before limits.
- The response owns its admission reservation. Transport failure before the
  first generator iteration still releases the conversation.
- The application opens resources only in lifespan. Failed database startup
  closes the provider already opened; normal shutdown closes resources in reverse
  order. Validation error text omits input values that might contain credentials.
- OpenAPI identifies the success response as `text/event-stream` and derives
  request/error shapes from Pydantic models.

## Test evidence

Initial CLI and API test runs failed because their planned modules were absent.
These are harness/import red results, not independent proof of later behavior.
The implementation was then checked through observable graph counters, iterator
closure, admission state and resource-open/close records.

Final owned-suite command:

```text
agent/.venv/Scripts/python.exe -m pytest -c agent/pyproject.toml agent/tests/unit/test_api.py agent/tests/unit/test_cli.py agent/tests/unit/test_lifespan.py -q
```

Result: **23 passed**, exit 0. This includes invalid/missing authentication before
generation, protected readiness, UUID/text/unknown-field validation, chunked and
declared body limits, exact body boundary, huge length text, SSE sequencing,
completion, conflict/capacity rejection, timeout cleanup, response-start failure,
generated OpenAPI, credential-free CLI help, defaults, reload configuration and
resource cleanup after normal/failed startup.

Pylint was run against owned production modules with
`--rcfile agent/pyproject.toml`; exit 0 before the final body-length regression
addition. The final repository-wide formatting/lint run remains authoritative.

Independent tester run against the graph/provider/database implementation:

```text
agent/.venv/Scripts/python.exe -m pytest -c agent/pyproject.toml agent/tests/unit/test_graph.py agent/tests/integration/test_memory.py agent/tests/integration/test_setup.py agent/tests/integration/test_provider_stream.py -q
```

Result: **12 passed**, exit 0. Tests used a disposable PostgreSQL instance and a
controlled provider. They covered SDK wire fields, custom graph streaming,
completed-history preservation, conversation separation, setup reruns, restricted
runtime privileges, provider failure/cancellation, fresh input and final-save
failure. No paid model call occurred.

## Environment limitation and resolution

The Windows sandbox blocked uv's default cache, PyPI resolution, Docker access
and event-loop socket creation. Test processes stalled inside Python's local
socket-pair construction before executing asynchronous tests. Approved execution
outside the sandbox resolved those infrastructure restrictions. The agent package
itself was not given a bypass or alternate production provider.

The pytest configuration must be selected explicitly when a root command spans
multiple suite directories. The supported pytest-asyncio loop-factory hook uses
the production Windows selector loop; it does not depend on the deprecated
event-loop-policy fixture.
