# Phase 4: Persistent memory and setup

Priority: P1. Status: completed. Estimate: 10 engineering hours. Dependency: Phase 3.

## Context and outcome

[Technical design](technical-design.md), [test plan](test-plan.md), [runtime research](reports/runtime-research.md). Use AsyncPostgresSaver as the only history store. Provision `agent_memory` on the existing PostgreSQL volume and preserve completed turns across restart.

## Planned files

| Action | Absolute target | Responsibility |
|---|---|---|
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/clients/checkpoints.py` | Runtime pool/saver ownership and readiness |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/setup_database.py` | Database/role provisioning, saver setup and grants |
| Modify | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/app.py` | Graph compilation with runtime saver and cleanup |
| Modify | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/cli.py` | Setup command execution |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/.env.example` | Runtime placeholders, native URL and named container runtime URL |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/.env.setup.example` | Native bootstrap/setup/runtime provisioning URLs and named container bootstrap/setup URLs |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/integration/test_memory.py` | Isolation, incomplete turns and final-save failures |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/integration/test_setup.py` | Existing-volume setup, rerun and restricted role |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/integration/test_cli_service.py` | Installed native CLI restart memory |

## Test-first steps

1. Write existing-volume tests with no agent database; then setup-rerun and actual runtime-role permission tests. Run against disposable PostgreSQL 17.
2. Implement fixed-identifier/autocommit database creation and setup-owner `await saver.setup()`. Grant runtime table/sequence rights and default privileges. Do not modify backend databases.
3. Write cross-conversation and native restart tests. Wire the runtime pool/saver through lifespan with one resource owner.
4. Write provider-failure, cancellation and final-checkpoint-write-failure tests. Inspect actual saver state and model input on fresh follow-up input.
5. Confirm no partial assistant output is successful history and no complete event precedes final save. Human input/pending state may remain; document the observed pinned behavior.
6. Verify setup without ModelArk/API-token values, runtime without administrative values and cleanup after partial startup. Refactor with tests passing.

## Checklist and acceptance

- [x] Existing-volume setup creates the separate database and can rerun without erasing turns.
- [x] Runtime role works with the real saver and cannot provision schema/roles/databases.
- [x] Native CLI restart preserves completed history; distinct UUIDs remain isolated.
- [x] Fresh input after failure is tested rather than resuming with `None`.
- [x] No parallel chat/request table, exactly-once guarantee or automatic replay is added.
- [x] Setup/runtime files are separate. The runtime password appears in setup provisioning inputs only as needed; admin values never enter runtime.

## Risk and next step

Savers can store human/pending input before generation fails. Manual resend may repeat context. No crash-resume guarantee is assumed. Retention is manual and isolated; shared volume deletion is forbidden. Continue to [Phase 5](phase-05-deployment-and-verification.md) after restart/failure/privilege tests pass.

## Completion evidence

Completed on 2026-11-02. The final combined suite passed all 63 tests. Formatting, lint, collection and repository checks passed. See [implementation verification](reports/implementation-verification.md) and the scoped reports it references.
