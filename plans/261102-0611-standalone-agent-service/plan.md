---
title: "Standalone agent service"
description: "Implement an independent streaming agent with persistent conversations and a local development command."
status: completed
priority: P1
branch: main
created: 2026-11-02
effort: "Provisional: 40 engineering hours; not a delivery commitment"
---

# Objective

Add an independent Python package in `agent/`. Use a one-node LangGraph conversation graph, direct ModelArk streaming through the OpenAI Python client, and PostgreSQL checkpoints. Provide a command-line interface (CLI) for native development without rebuilding an image. All five implementation phases are complete. [Verification evidence](reports/implementation-verification.md) records the final 63 passing tests and independent reviews.

## Scope and decisions

- The selected application programming interface (API) accepts a conversation identifier and message and returns Server-Sent Events (SSE), a text event stream over Hypertext Transfer Protocol (HTTP).
- A shared service secret protects generation. User login and conversation ownership remain the calling backend's responsibility.
- Persist graph state in `agent_memory` inside the existing PostgreSQL container. Keep separate setup-owner and restricted runtime credentials.
- Use one serving worker and one active agent instance. Native and container agents must not serve the same conversations concurrently.
- Exclude tools, retrieval, voice, optical character recognition, Model Context Protocol, frontend work and user login. No factory evidence is available to this version.
- Write conventional unit, integration and container tests before the corresponding production code. No paid provider calls occur by default.

## Phases

| Phase | Outcome | Estimate | Dependency | Status |
|---|---|---|---|---|
| [1. Package and CLI](phase-01-package-and-cli.md) | Isolated package, test harness and credential-free command help | 6 h | None | Completed |
| [2. API and access](phase-02-api-and-access.md) | Typed contracts, authorization and bounded admission | 6 h | Phase 1 | Completed |
| [3. Graph and streaming](phase-03-graph-and-streaming.md) | Direct provider stream and controlled completion | 10 h | Phase 2 | Completed |
| [4. Persistent memory](phase-04-persistent-memory.md) | Repeatable database setup and restart-safe completed turns | 10 h | Phase 3 | Completed |
| [5. Deployment and verification](phase-05-deployment-and-verification.md) | Container path, native reload, documentation and verified suites | 8 h | Phase 4 | Completed |

Each phase uses red-green-refactor: write a behavior test, observe its intended failure, implement it, then refactor with tests passing. The provisional total is 40 engineering hours. Provider compatibility or checkpoint migration findings may change this estimate.

## Design and tracking

[Technical design](technical-design.md) defines the selected contract and illustrative code. [Test plan](test-plan.md) defines deterministic fixtures and completion evidence. [Task ledger](tasks.md) records phase and critical-task dependencies. Session task tools are unavailable; the ledger does not claim task-tool hydration.

Research: [runtime findings](reports/runtime-research.md) and [development findings](reports/development-research.md). The direct Python client and custom-stream decisions supersede the alternative adapter example in the runtime report. [Independent review](reports/plan-review.md) records findings and verified plan corrections.

## Completion criteria

- Native `denso-agent serve --reload` uses the same app factory as the image. Help opens no resources and requires no credentials. Setup requires database credentials only.
- Genuine network streaming, final-save failures, disconnect cleanup, fresh input after failure and cross-conversation separation are verified.
- Completed conversation state survives native process and container restart. Setup works on existing volumes and preserves data on rerun.
- Unit tests need no Docker, network or real secrets. Selected integration/container suites fail clearly if Docker is missing.
- Runtime uses only restricted database credentials. Generation rejects invalid service secrets before provider or database work.
- Documentation examples run against test fixtures. No replay, exactly-once execution, automatic retry or crash-resume guarantee is claimed.
- Only intended application files change during implementation. Existing backend behavior and data remain intact.

## Rules and handoff

Applicable principles come from [development rules](D:/STARTUP/hex/hex/.claude/rules/development-rules.md), [documentation rules](D:/STARTUP/hex/hex/.claude/rules/documentation-management.md) and [presentation rules](D:/STARTUP/hex/hex/.claude/rules/answering-and-presentation-rules.md). Use narrow modules, explicit shapes, thin handlers, bounded state, one credential check, owned resource cleanup and literal professional English. Hex-specific commands, test metrics, `ToolResult` and repository structure are not copied.

Implementation handoff: `/cook D:/HACKATHONS/DENSO/denso-hackathon/plans/261102-0611-standalone-agent-service/plan.md`.
