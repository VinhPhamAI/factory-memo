# Persistent task ledger

Status: completed. This ledger replaces unavailable session task-creation tooling. It does not claim that tasks have been hydrated into a task service. All targets and acceptance criteria are defined in the linked phase files.

| ID | Task | Depends on | Estimate | Status |
|---|---|---|---|---|
| P1 | [Package, CLI and harness](phase-01-package-and-cli.md) | None | 6 h | Completed |
| P2 | [API contracts and access](phase-02-api-and-access.md) | P1 | 6 h | Completed |
| P3 | [Graph/provider streaming](phase-03-graph-and-streaming.md) | P2 | 10 h | Completed |
| P4 | [Persistent memory/setup](phase-04-persistent-memory.md) | P3 | 10 h | Completed |
| P5 | [Deployment/docs/verification](phase-05-deployment-and-verification.md) | P4 | 8 h | Completed |

Total provisional phase estimate: 40 engineering hours. Critical tasks below are included in the phase estimates and must not be added again.

| ID | Critical task | Parent phase | Depends on | Completion evidence | Status |
|---|---|---|---|---|---|
| C1 | Lock compatible SDK/graph/saver versions | P1 | None | Frozen sync and package-isolation tests | Completed |
| C2 | Prove auth rejects before protected work | P2 | P1 | Provider/checkpoint counters zero on rejection | Completed |
| C3 | Prove direct provider request fields | P3 | P2 | HTTP stub captures stream/max_tokens/retries behavior | Completed |
| C4 | Prove incremental TCP output and cleanup | P3 | C3 | First delta before barrier release; resources close | Completed |
| C5 | Prove existing-volume setup and runtime privileges | P4 | P3 | Real PostgreSQL setup rerun and privilege tests | Completed |
| C6 | Prove incomplete-state policy and final-save ordering | P4 | C4, C5 | Actual checkpoint/model-input assertions after failure | Completed |
| C7 | Prove native restart and temporary-copy reload | P5 | P4 | Context retained; process/port cleanup verified | Completed |
| C8 | Prove container startup isolation and restart | P5 | P4 | Only intended services/secrets; persisted memory | Completed |
| C9 | Verify docs, auth mutation and all suites | P5 | C7, C8 | Example execution, mutation failure and restored passing gates | Completed |

A parent phase completes only after its critical tasks pass. A critical task never depends on completion of its own parent phase. Phase dependencies and earlier critical-task dependencies determine execution order.

Evidence under `reports/` records initial harness failures, independently reproduced behavior regressions, passing targeted checks and the final combined suites. Harness/import failures are distinguished from behavior failures. Completion is supported by execution results and independent review, rather than document creation.

## Final evidence

All five phases and nine critical tasks are complete. The final frozen-package command passed 63 tests in 125.03 seconds, exit 0. See [implementation verification](reports/implementation-verification.md) for commands, reviews, cleanup checks and operating limits.
