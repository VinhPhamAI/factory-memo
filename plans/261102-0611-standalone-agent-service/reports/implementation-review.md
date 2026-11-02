# Implementation review and independent test run

Date: 2026-11-02.

## Scope and independence

The reviewer implemented package, CLI and API files, so those files are not
presented as independently reviewed here. Their tests and resource checks are
recorded in [package/API verification](package-api-verification.md). This review
independently inspected the provider, graph, PostgreSQL setup, checkpoint resource
ownership and corresponding tests implemented by other contributors.

The review traced provider input, output deltas, completion acknowledgement,
checkpoint writes, cancellation, database privileges and test-owned cleanup.
The approved technical design and test plan supplied the acceptance criteria.

## Findings and resolutions

| Finding | Resolution | Verification |
|---|---|---|
| An HTTP transport failure after a stream opens can be a raw `httpx.HTTPError`, rather than an OpenAI `APIError`. It would otherwise be mislabeled as a persistence failure. | The provider normalizes HTTP transport and malformed response failures into `provider_error`. | `test_network_failure_during_stream_is_provider_error` passes in the final unit run. The stream is explicitly closed and its private diagnostic is absent from the public error. |
| A malformed stream could replace an earlier filtered/limited finish marker with a later `stop`. | The provider rejects conflicting finish markers. | `test_conflicting_finish_markers_never_turn_failure_into_success` passes. The primary implementer also recorded a failing regression before the guard was added. |
| Windows psycopg asynchronous connections require a selector event loop, including native reload child processes. | The CLI uses an explicit Uvicorn loop factory and setup uses the same factory through `asyncio.Runner`. Tests use the locked pytest-asyncio loop-factory hook. | Real PostgreSQL tests ran through the ordinary pytest command, without a separate policy wrapper. |
| Response cleanup could release a later request's reservation after the old generator finished but its final body send was still pending. | Admission stores an opaque identity per active request. Response cleanup releases only its captured identity; it cannot discard a newer reservation for the same conversation. | A controlled final-send regression first failed with active count 0 instead of 1, then passed. All 16 API tests passed after the correction. |

### Windows reload diagnosis

The real installed Windows CLI detected source changes but its Uvicorn supervisor
blocked while joining the existing child. Temporary diagnostics were inserted
only into the test's editable package copy. The serving child had a console
window and code page; resetting its inherited Ctrl+C ignore state through
`SetConsoleCtrlHandler(NULL, FALSE)` returned success. Reload still stalled. This
excluded the proposed inherited-ignore-state explanation. The temporary
instrumentation was removed from the test source after reproduction.

The deployment implementation therefore requires an explicitly bounded Windows
supervisor stop/restart path rather than a console-state reset or test bypass.
The written `WindowsReload` helper was reviewed after implementation: terminate
and kill each have bounded joins; a still-running old child prevents replacement;
shutdown closes bound sockets even if stopping fails; and the watcher run path
cleans up on startup/restart failure. The CLI selects this helper only for Windows
reload, retaining the same factory, one worker and psycopg-compatible loop. Imports
open no resources and command help remains credential-free. The real-CLI reload
result remains the deployment session's final acceptance check.

No unresolved critical or high-priority issue was found in the independently
reviewed scope after these corrections.

## Verified behavior

- SDK calls use the configured endpoint/model, `stream=True`, `max_tokens` and
  disabled retries. A controlled HTTP server captures the actual wire payload.
- Only standard visible content reaches the graph writer. Private reasoning is
  excluded. The 32,000-character limit is checked before append and emission.
- Provider streams close on ordinary completion, overflow, failure and
  cancellation. Successful assistant state is returned only after nonempty stop.
- Graph execution consumes fresh human input and custom v2 streams with
  synchronous durability. Public completion follows successful exhaustion.
- PostgreSQL setup validates isolated database/role targets, uses quoted fixed
  identifiers, migrates through the owner and grants restricted runtime access.
- Rerunning setup retains completed conversation state. Runtime cannot provision
  a database, role or schema. The migration ledger is not writable by runtime.
- Provider failure and cancellation preserve no provisional assistant message.
  Fresh input works after these failures. Final-save failure emits an error and
  permits a subsequent fresh request; no rollback guarantee is invented.
- Tests use unique, disposable PostgreSQL resources. Cleanup addresses the
  test-owned container and its volumes; it never resets the developer stack.

## Commands and results

Independent graph/provider/database selection:

```text
agent/.venv/Scripts/python.exe -m pytest -c agent/pyproject.toml agent/tests/unit/test_graph.py agent/tests/integration/test_memory.py agent/tests/integration/test_setup.py agent/tests/integration/test_provider_stream.py -q
```

Result: **12 passed**, exit 0, 11.10 seconds.

Final complete offline unit selection:

```text
agent/.venv/Scripts/python.exe -m pytest -c agent/pyproject.toml agent/tests/unit -q
```

Result after the reservation-race and Windows reload corrections: **48 passed**,
exit 0, 3.10 seconds. This includes transport-error, conflicting-finish,
old-response reservation cleanup and Windows supervisor/CLI regressions. No paid
provider call occurred.

## Remaining release gates

The primary session remains responsible for recording the final complete
integration/end-to-end results, Docker image checks, native reload, documentation
commands, authentication mutation, formatting, lint and `git diff --check`.
This scoped review does not mark those unobserved gates complete.

For the cook workflow's numeric review gate, the independently reviewed scope is
assessed at **9.6/10**, with **zero unaddressed critical findings**. The assessment
rests on the observable failure-state and real PostgreSQL proofs above; it is not
a substitute for the remaining release gates or a production-readiness claim.
