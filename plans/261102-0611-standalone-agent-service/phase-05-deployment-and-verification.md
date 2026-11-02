# Phase 5: Deployment, documentation and verification

Priority: P1. Status: completed. Estimate: 8 engineering hours. Dependency: Phase 4.

## Context and outcome

[Technical design](technical-design.md), [test plan](test-plan.md), [development research](reports/development-research.md). Deliver both native reload and independent container execution. Document verified commands and limits in professional impersonal English.

## Planned files

| Action | Absolute target | Responsibility |
|---|---|---|
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/Dockerfile` | Frozen independent nonroot image |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/.dockerignore` | Exclude local secrets, virtual environments and caches |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/deployments/agent.compose.yml` | Existing PostgreSQL, one setup job and agent dependency order |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/README.md` | Prerequisites, native commands, container mode and tests |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/docs/api.md` | Typed API/SSE, errors, trust boundary and examples |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/docs/development.md` | Structure, test-first work, reload, setup/upgrade and limitations |
| Modify | `D:/HACKATHONS/DENSO/denso-hackathon/README.md` | Short standalone-service documentation link |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/.github/workflows/agent.yml` | Conventional independent-package checks |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/e2e/test_container_service.py` | Image stream, restart, setup rerun and secret isolation |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/e2e/test_reload.py` | Temporary-copy native reload and process cleanup |

## Test-first steps

1. Write container tests using PostgreSQL 17/provider stub in a uniquely named disposable project. Assert only postgres/setup/agent start and unrelated backend credentials are unnecessary.
2. Implement image and overlay. Reuse current developer Compose project only for actual development. Explicit container-URL mappings permit native-to-container switching without editing env files; explicit runtime mappings exclude administrative credentials. Test switching only after the native serving process stops.
3. Write native reload test using a temporary editable copy. Confirm source-triggered process replacement, surviving memory and cleanup without modifying repository source.
4. Specify documentation command/payload tests. Write README/API/development docs with exact PowerShell/curl payloads, setup-only/runtime settings and host/container addresses.
5. Inspect automation conventions, add the agent workflow and execute unit/integration/e2e suites with no paid calls. Test setup rerun and container restart.
6. Mutate the shared authentication gate temporarily; verify the rejection test fails. Restore only the mutation and rerun. Record test exits, resource cleanup and `git diff --check`.

## Checklist and acceptance

- [x] Image uses the same factory, one worker, no reload and no backend imports.
- [x] Native source edits reload without rebuilding the image; active-answer interruption is documented.
- [x] Admin/setup secrets are absent from runtime. Compose uses explicit credentials and proper startup ordering.
- [x] True TCP streaming, native/container restart, setup rerun and disconnect cleanup pass against fixtures.
- [x] OpenAPI derives from schemas. SSE and failure documentation match observed behavior.
- [x] Live calls require explicit opt-in and never replace the integrated suite. Missing Docker fails selected infrastructure suites clearly.
- [x] Test cleanup affects only test-owned resources. No shared `down -v` operation occurs.

## Risk and next step

Native and container modes must not run concurrently against the same conversations. Reload may interrupt generation. Checkpoint upgrades require backup, stopped runtime, one setup process and verified compatibility. Acceptance evidence and independent reviews passed. Implementation is complete.

## Completion evidence

Completed on 2026-11-02. The final combined suite passed all 63 tests. Formatting, lint, collection and repository checks passed. See [implementation verification](reports/implementation-verification.md) and the scoped reports it references.
