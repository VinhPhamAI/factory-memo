# Database implementation evidence

The checkpoint database uses fixed names: database `agent_memory`, owner
`agent_owner`, runtime role `agent_runtime`. Bootstrap credentials cannot name
either agent role. Setup rejects another existing database owner and existing
agent-role memberships before changing credentials. No backend database is
modified.

Setup runs saver migrations using the owner. Runtime receives database CONNECT,
schema USAGE, checkpoint data operations and sequence access. Runtime receives
SELECT only on the migration ledger. Default grants cover future owner-created
checkpoint objects. Rerunning setup preserves existing checkpoint data.

## Test evidence

- Initial collection failed because the checkpoint module did not yet exist.
- The first real PostgreSQL run exposed a Windows compatibility error:
  psycopg asynchronous connections reject the default Proactor event loop.
  CLI and test-loop handling were referred to the package implementation.
- With the Windows selector loop, the initial six setup/history tests passed.
- After adding role-safety and future-grant coverage, the combined database suite
  passed: **14 tests, 9.05 seconds, exit 0**.
- Pylint on `setup_database.py` and `clients/checkpoints.py` exited 0.

The passing command used the independently installed interpreter:

```powershell
agent/.venv/Scripts/python.exe -c "import asyncio,pytest; asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy()); raise SystemExit(pytest.main(['agent/tests/integration/test_setup.py','agent/tests/integration/test_memory.py','agent/tests/unit/test_setup_validation.py','-q']))"
```

Tests started PostgreSQL 17 in a uniquely named disposable Docker container with
an allocated host port. Cleanup removed only that container and its anonymous
volumes. The shared development database and volume were not touched.

## Observed checkpoint behavior

Completed human and assistant turns survive closing the pool, rerunning setup
and rebuilding the graph with a new pool. Different conversation identifiers
do not share messages.

After provider failure or cancellation, the saver retains the human input.
Provisional assistant fragments are absent from saved successful history. A
fresh input produces a fresh answer with the retained human input in context.
Provider cleanup and admission release are asserted.

A controlled failure in the final real-saver `aput` produces provisional text
and an error, with no completion acknowledgement. The subsequent fresh request
works. Tests reject provisional assistant history but allow an unacknowledged
full answer, because pending writes can survive final checkpoint failure.
These tests establish no rollback, replay or exactly-once guarantee.

Native process restart, Docker image restart and real network streaming are
separate package/deployment gates; a pool restart is not presented as their
replacement.

The subsequent installed-native-process restart test passed independently:
**1 passed in 24.62 seconds, exit 0**. It verifies a new serving process after
setup rerun, retained context and another conversation's isolation. See
`test_cli_service.py` and `api-implementation-review.md`.
