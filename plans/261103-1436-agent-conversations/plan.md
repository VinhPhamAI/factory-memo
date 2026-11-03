# Agent and conversations implementation plan

Status: phase 1 (text chat and history) implemented and verified with a controlled model provider; speech, photos and retrieval not started. Scope: Section 4 of the current product checklist. Review: [phase 1 code review](../reports/code-reviewer-261103-1906-agent-conversations-phase-1.md).

Start from a branch containing auth merge `a5ef5f5`. Apply the application
migrations, start the standalone agent with its own database, and verify real
signup/login and `GET /auth/me` before the browser smoke test.

The browser uses the backend for user authentication and conversation access. The backend stores conversation metadata in the application database and forwards authorized requests to the standalone agent. The agent reads and writes messages through LangGraph in agent_memory. The existing asynchronous worker can serve several conversations, with at most one active generation per conversation and a configurable process-wide limit.

## Phases

1. [Text chat and history](phase-01-text-chat.md) — CRUD, authorization, checkpoint history, and streaming.
2. [Microphone and photo input](phase-02-inputs.md) — editable transcripts and authorized photo attachments.
3. [Retrieval tool](phase-03-retrieval-tool.md) — controlled integration first, then real search and citations.
4. [Integration and verification](phase-05-verification.md) — native and container checks and the knowledge smoke test.

Phase 1 provides the chat path. Speech service verification can run in parallel
using the merged AI gateway. Photos need the authorized files API and verified
image support. Build the retrieval tool with controlled results, then connect
the live search and evidence APIs when available. The [inquiry draft plan](phase-04-inquiry-draft.md)
belongs to the Inquiries section.

## Checklist closeout

| Item | Evidence needed to check it off |
|---|---|
| Conversation CRUD | Phase 1 real login smoke test covers create, list, reopen, rename, delete, and cross-user denial before agent calls |
| Agent history API | Phase 1 restarts the agent and verifies ordered messages from the same checkpoint |
| Speech-to-text service | Phase 2 real Vietnamese fixture returns usable text through the gateway and a failure reaches the caller |
| Chat inputs and speech-to-text UI | Phase 2 verifies text, editable transcript and failure display, and live owned-photo send plus foreign-photo rejection |
| Streaming | Phase 1 real HTTP test shows early deltas, authoritative replacement after save, and visible errors |
| Retrieval tool | Phase 3 live search verifies trusted scope, clickable evidence for a known fault, and missing-evidence behavior |
| Knowledge smoke test | Final pass uploads and ingests a real fixture, opens the citation, removes the file from the library, then reopens the citation as an authorized reader |

Record each item as passed, failed, or blocked. Controlled tests can prove local
behavior while a dependency is pending. Keep the full item open until its
listed evidence is available.

## Implementation rules

- Follow [project guidance](../../AGENTS.md), [engineering standards](../../docs/development/code-standards.md), and [agent development](../../agent/docs/development.md). File additions and endpoints in the phases are proposed. Code sketches illustrate boundaries.
- Keep the standalone package independent and use its existing async ModelArk client and checkpoint setup. Check the exact model’s image and tool support before live integration. Controlled providers are the default for tests.
- Use existing app ORM models and migrations. History adds an HTTP response type and reads existing checkpoints. Revisit storage only if a concrete requirement needs a schema change.
- Read whole checkpoint state initially and bound the public response. Measure history cost before adding a new storage layer. Reassess conversation admission across instances before deploying more than one worker.
- Treat source material as evidence, preserve provenance and uncertainty, and keep model reasoning and service credentials private.
