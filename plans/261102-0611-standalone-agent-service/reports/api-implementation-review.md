# Independent API and CLI review

Scope: application factory, command dispatch, configuration, authentication,
request/body schemas, admission, SSE framing and response ownership. The reviewer
did not implement these modules. Database implementation is reviewed separately.

## Findings and corrections

1. A Content-Length header with more than Python's integer digit limit could
   raise ValueError during size checking. The middleware now compares normalized
   decimal byte strings against the body cap. The regression test sends a
   5,000-digit declared length and requires HTTP 413.
2. Pydantic validation errors could display a malformed connection URL despite
   `repr=False`. The shared settings configuration now enables
   `hide_input_in_errors=True`. An independent invalid-URL check confirmed that
   a distinctive password marker is absent from the exception text.

Both corrections were read and checked after implementation.

3. Further edge-case review identified a reservation race. Runtime cleanup and
   response cleanup both released by conversation identifier. A new request
   admitted during the old response's final transport write could lose its
   reservation when the old response cleaned up again. An independent controlled
   transport reproduced this: active reservations became 0 after a new request
   had acquired the identifier; the expected value was 1 (exit 1). The response
   now retains an opaque reservation identity and releases only that identity.
   Stale response cleanup cannot remove a newer request. The reviewer read this
   correction and independently reran all API tests: **16 passed in 0.65 seconds,
   exit 0**, including the controlled final-body-send regression.

No unresolved actionable finding remains in this review scope after these checks.

## Evidence

The reviewer independently ran:

```text
agent/.venv/Scripts/python.exe -m pytest agent/tests/unit/test_api.py agent/tests/unit/test_lifespan.py agent/tests/unit/test_cli.py -q
```

Result: **23 passed in 2.73 seconds, exit 0**.

The reviewed code centralizes constant-time token checking. Routes reserve
capacity before starting the response. The response owns cleanup even if the
transport fails before generator startup. Application lifespan owns clients and
closes already-opened resources if later startup fails. The native command uses
the same factory as the image and selects a psycopg-compatible Windows event
loop in reload child processes.

Native CLI restart was separately executed against PostgreSQL and the local
HTTP provider fixture: **1 passed in 24.62 seconds, exit 0**. This gate starts an
installed CLI process, completes a turn, stops it, reruns setup, starts a different
process and verifies retained context plus conversation separation.

Full network, image, reload and final combined-suite evidence remain separate
verification gates. This scoped review does not replace those tests.
