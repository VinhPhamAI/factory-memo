# Runtime implementation evidence

The service uses the locked LangGraph 1.2.19, checkpoint-postgres 3.2.0 and
OpenAI Python client 2.54.0. The real graph tests verify `version="v2"`, custom
streaming and `durability="sync"` against these installed versions.

## Provider and graph

- A real local HTTP server captures the SDK request. It receives Chat
  Completions, `stream: true`, `max_tokens`, the configured model and the test
  provider bearer key. No adapter rewrites the token field.
- A controlled barrier proves that the first SDK content fragment is consumed
  before the provider is allowed to send its remaining output. Container tests
  separately exercise the complete public API over TCP.
- Unit tests cover incomplete finish reasons, empty output, the 32000-character
  boundary across many fragments, overflow before emission and cancellation.
- The system instruction is added to generation input. Saved messages are not
  trimmed or supplemented with repeated system messages.

The test files were written before their corresponding initial runtime modules.
The first attempt to run them was blocked by sandbox interpreter-cache access;
that attempt was not a successful behavioral red test. Subsequent runs use the
independent installed environment with elevated tool access. This distinction is
recorded rather than claiming an uninterrupted red-green sequence.

Two later regressions have observed behavioral red and green evidence:

1. A raw `httpx.ReadError` during an SDK stream escaped provider normalization.
   The test failed with the transport error. Catching transport/parse failures
   now emits a sanitized `provider_error`; the stream still closes.
2. Conflicting finish markers could overwrite an earlier filtered result with
   `stop`. The regression failed because no exception was raised. Conflicting
   markers are now rejected before a successful answer can be returned.

## Authentication mutation

A temporary copy of `agent/src` was placed first on a subprocess's `PYTHONPATH`.
Only that copy's shared authentication condition was bypassed. The ordinary
`test_auth_rejects_before_graph` failed with `assert 200 == 401` and pytest exit 1.
The temporary copy was removed. Production source was never changed by the
mutation. The final ordinary suite checks the intact gate again.

## Verification before final deployment checks

From the repository root:

```text
uv --cache-dir D:/HACKATHONS/DENSO/.uv-agent-cache run --project agent --frozen pytest -c agent/pyproject.toml agent/tests/unit agent/tests/integration -q
```

Result: 53 passed in 35.76 seconds, exit 0. This includes 43 unit and 10
integration cases, actual PostgreSQL 17 and native CLI process restart.
Collection found 58 tests including five end-to-end cases. Later review fixes
and final suite results are recorded in the final verification report.

No paid ModelArk request was made. Account/model compatibility still requires
explicit live verification with the intended credentials. Provider fixtures
prove the implemented wire contract and failure handling, not production model
quality or manufacturing accuracy.
