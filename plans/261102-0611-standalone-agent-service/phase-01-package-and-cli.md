# Phase 1: Package, CLI and test harness

Priority: P1. Status: completed. Estimate: 6 engineering hours. Dependency: none.

## Context and outcome

[Plan](plan.md), [technical design](technical-design.md), [test plan](test-plan.md), [development research](reports/development-research.md). Create an independently installed `agent/` package and credential-free command help. Native development must require no image rebuild for source changes.

## Planned files

| Action | Absolute target | Responsibility |
|---|---|---|
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/pyproject.toml` | Independent build, console script, minimal dependencies and paid-test marker |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/uv.lock` | Tested dependency resolution |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/cli.py` | argparse serve/setup-db dispatch |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/config.py` | Separate runtime and setup settings |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/conftest.py` | Test-only fixtures with no automatic container startup for unit tests |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/unit/test_cli.py` | Help, defaults, settings selection and import behavior |
| Modify | `D:/HACKATHONS/DENSO/denso-hackathon/.gitignore` | Ignore local setup env and allow agent lock |

Add package initialization files and skeleton factory/setup modules only as needed to expose the chosen command. Production resources remain unopened until their lifecycle phases.

## Test-first steps

1. Specify help/default/invalid-command tests and an import test that fails if resources open. Specify setup settings without ModelArk/API-token values.
2. Run tests and observe missing command/package behavior. Create build metadata and lock compatible versions.
3. Implement minimal dispatch and validated settings. `serve` targets `denso_agent.app:create_app`; `setup-db` loads only setup settings.
4. Implement source-derived reload path and one-worker setting. Add test-only provider fixtures for later suites.
5. Run unit tests and verify package imports contain no `backend.*`. Refactor only with targeted tests passing.

## Checklist and acceptance

- [x] Installed command exposes help without credentials or network access.
- [x] Runtime/setup settings have separate requirements and env examples.
- [x] Frozen sync uses the independent lock; root backend dependencies are not installed implicitly.
- [x] Unit test collection starts no containers. Directory commands collect unmarked tests; the live marker is registered and paid calls excluded by default.
- [x] Local setup secrets are ignored; examples contain no real credentials.
- [x] New or modified functions/classes have concise, purpose-specific docstrings with relevant `Args:`, `Returns:` and `Raises:` sections and no contextless filler.

## Risk and next step

Dependency versions may expose different stream signatures. The lock and later integration tests are authoritative. Avoid copying Hex commands or metrics. Continue to [Phase 2](phase-02-api-and-access.md) after the CLI/harness contract passes.

## Completion evidence

Two additional focused modules support the verified native Windows path:
`event_loop.py` selects a psycopg-compatible loop, including reload children;
`windows_reload.py` supplies bounded worker replacement because the locked
Uvicorn supervisor can wait indefinitely for a console signal. Helper regression
tests and the actual installed CLI reload test pass. Both modes retain the same
application factory and one-worker restriction.

Completed on 2026-11-02. The final combined suite passed all 63 tests. Formatting, lint, collection and repository checks passed. See [implementation verification](reports/implementation-verification.md) and the scoped reports it references.
