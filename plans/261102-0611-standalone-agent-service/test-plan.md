# Test plan

Status: completed. Final execution passed 63 tests; see [implementation verification](reports/implementation-verification.md). Tests precede corresponding implementation. Use conventional pytest functions and fixtures. No custom evaluation framework is required. Dependencies and test commands must use the independent `agent/` package.

## Suites

| Directory / marker | Infrastructure | Main checks |
|---|---|---|
| `tests/unit/` | In-process fakes only | Contracts, settings, authorization, limits and resource ownership |
| `tests/integration/` | Temporary PostgreSQL 17 and deterministic provider HTTP server | Real SDK, LangGraph, saver, API and native command |
| `tests/e2e/` | Real TCP service, temporary reload copy or agent image | Incremental streaming, process/container restart and deployment |
| `live` | Explicit paid-provider opt-in | Selected ModelArk compatibility only |

Select the three main suites by directory only. An ordinary unmarked test placed in a suite directory must be collected by its documented command. Register the `live` marker in `agent/pyproject.toml`; default selection excludes it, with an additional explicit environment opt-in protecting paid tests. Unit tests require no Docker, network or credentials. Selected integration/container tests fail at fixture setup if Docker is unavailable; they never silently skip or replace PostgreSQL with an in-memory saver. Test credentials are placeholders. Fakes and monkeypatching remain test-only.

## Test-first sequence

1. Package/CLI tests precede package and console-command implementation.
2. API/authentication tests precede handlers and admission logic.
3. Provider/graph tests precede stream execution and public event mapping.
4. Real-saver/setup tests precede bootstrap, migrations and persistence integration.
5. Native reload/container/docs tests precede deployment wiring and final documentation.

After the initial harness, each red result must fail for the behavior under test, not an unrelated import or fixture failure. Implement the smallest passing behavior, run the targeted suite, then refactor. Record exit status and collected cases. Do not hide failures behind shell output pipelines.

## Unit cases

- CLI help and invalid commands need no secrets or resources. `serve` selects runtime settings; `setup-db` selects setup settings without ModelArk/API-token requirements.
- Factory imports open no resources. Lifespan opens resources once and closes them on shutdown or partial startup failure.
- Runtime/setup env separation, nonempty secrets and configuration bounds are enforced. Reload watches package source only; worker count stays one.
- UUID validation, trimmed text boundaries, unknown fields and body cap, including chunked bodies, match the API contract.
- Missing/invalid token produces 401 before provider/checkpoint calls. Readiness requires auth; health exposes no private details.
- SSE schemas and JSON errors validate. Event sequence increases. Only standard content appears; reasoning and raw provider errors do not.
- A nonempty `stop` is successful. `length`, `content_filter`, missing finish reason and empty output produce error without successful assistant state.
- Final persistence acknowledgement precedes completion. Errors never produce a second terminal event.
- Same-conversation rejection is 409; global saturation is 429. Active identifiers, semaphore capacity and upstream streams release after all terminal paths.
- Recent-context selection preserves persisted messages and adds the system instruction only to generation input. The accumulated answer cap is 32000 characters measured by Python `len`. Test below, exactly at and one character beyond the cap, including many fragments. Overflow closes upstream, restores capacity and emits `output_limit` without completion.
- Direct SDK requests use Chat Completions, `stream=True`, `max_tokens`, explicit endpoint/model/key, timeout and retries zero. No adapter rewrites token fields.

Use observable close counters, blocked-task events and pool/admission state. Response JSON alone does not prove cleanup.

## Integration cases

- Run actual AsyncOpenAI against a deterministic HTTP stub and actual LangGraph against temporary PostgreSQL. Stub requests record wire fields and messages.
- Confirm conversation memory across turns and native command restart. Another conversation must not receive prior context.
- Confirm setup on a previously initialized PostgreSQL volume without `agent_memory`. Repeat setup after a completed turn; data remains.
- Confirm runtime grants allow saver reads/writes but forbid role/database/schema creation. Setup works without ModelArk configuration.
- Fail provider after first delta, cancel while blocked and fail final checkpoint write. Confirm no completion, resource release and fresh-request admission.
- Inspect actual checkpoint state/model input after each failure and new input. Fresh input is not `None`; pending human input may remain. Partial assistant output must not appear as successful history.
- Saturate same-conversation/global controls before opening additional streams. Verify no extra provider request reaches the stub and capacity returns after release.
- Readiness database probes are bounded and never call the model. Provider context-limit errors are clear without pretending generic character limits count exact tokens.
- Native CLI subprocess runs with installed package and restarts against the same test database. Use readiness deadlines and hidden Windows process windows.

## Real-network streaming proof

A provider stub emits a first delta, waits on a test-controlled barrier, then emits the remainder and stop marker. A real TCP client must receive the first public text event before that barrier is released. This detects buffering without flaky latency assertions. In-process HTTP clients alone are insufficient.

Illustrative pytest pseudocode; fixtures, authentication, event parser and bounded cleanup are omitted:

```python
async def test_first_delta_precedes_provider_release(agent_url, provider, client):
    async with client.stream("POST", agent_url, json=valid_request(),
                             headers=service_headers()) as response:
        event = await next_text_event(response, deadline=TEST_DEADLINE)
        assert event.delta == "first"
        assert not provider.release.is_set()
        provider.release.set()
        events = await collect_remaining_events(response)
        assert terminal_names(events) == ["complete"]
```

The fixture enforces a deadline and releases/cancels the provider in `finally` even if an assertion fails. Tests inspect arbitrary network chunk boundaries rather than assuming one TCP read equals one SSE event.

## Restart and setup proof

Illustrative pytest pseudocode; process fixtures, checkpoint inspection and credential setup are omitted:

```python
async def test_setup_rerun_and_restart_preserve_memory(database, provider):
    await run_setup(database.setup_env)  # No model key or service token.
    async with native_service(database.runtime_env, provider.url) as first:
        await complete_turn(first, CONVERSATION, "Remember the code amber.")
    await run_setup(database.setup_env)
    async with native_service(database.runtime_env, provider.url) as restarted:
        await complete_turn(restarted, CONVERSATION, "Recall the code.")
    assert provider.last_request_contains("Remember the code amber.")
    assert await database.has_completed_assistant_turn(CONVERSATION)
```

The deterministic provider records context; assertions do not depend on real model wording. Verify another UUID excludes the remembered turn.

## End-to-end and documentation

- Start a unique disposable Compose project using PostgreSQL 17, provider stub and actual agent image. Launch the agent target only. Assert backend API/worker/speech do not run and unrelated credentials are unnecessary.
- Inspect runtime environment: setup/admin secrets must be absent. Confirm setup completes before agent starts and health/readiness work.
- Use the same unchanged runtime/setup files for a completed native turn, stop native serving, then start the image. Confirm that the explicit container URL mappings reach the same database and preserve the turn. Do not run both serving modes simultaneously.
- Verify actual network streaming, container restart memory, setup rerun, provider failure and disconnect cleanup.
- Run native reload from a temporary editable project copy. Change only that copy's watched source; observe serving-process replacement and surviving checkpoint memory. Terminate the reload process tree and confirm port release.
- Execute curl/PowerShell examples against fixture services. Report unavailable shell coverage explicitly. No real keys or production fake switches are introduced.
- Use test-owned sockets or bounded bind retries, explicit events and deadlines. Cleanup in fixture `finally` removes only test-owned resources, never the shared developer volume.

## Planned commands and final evidence

From repository root after implementation:

```text
uv sync --project agent --frozen --group dev
uv run --project agent pytest -c agent/pyproject.toml agent/tests/unit -q
uv run --project agent pytest -c agent/pyproject.toml agent/tests/integration -q
uv run --project agent pytest -c agent/pyproject.toml agent/tests/e2e -q
uv run --project agent pytest -c agent/pyproject.toml agent/tests --collect-only -q
uv run --project agent black --check agent/src agent/tests
uv run --project agent pylint --rcfile=agent/pyproject.toml agent/src/denso_agent
```

Lock Black and Pylint as development dependencies. CI uses these commands, the same suites and deterministic provider. Live smoke requires `AGENT_RUN_LIVE=1` and explicit `-m live`; default commands must not spend tokens. No borrowed score or test-count threshold is assumed.

Temporarily mutate the common authentication gate in an isolated copy or exact reversible patch. The unauthorized test must fail because protected execution becomes reachable. Restore only the mutation, then verify passing behavior. Never discard unrelated work to reverse a probe.

Completion evidence records red/green cases, all selected suite exits, collection, native/container restarts, setup rerun, privilege checks, genuine streaming and cleanup assertions. Run `git diff --check`. A paid smoke result never substitutes for the integrated suite.
