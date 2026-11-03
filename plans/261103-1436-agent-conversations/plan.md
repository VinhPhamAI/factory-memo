# Agent and conversations implementation plan

Status: proposed. Scope: Section 5 of the product checklist.

The browser uses the backend for user authentication and conversation access. The backend stores conversation metadata in the application database and forwards authorized requests to the standalone agent. The agent reads and writes messages through LangGraph in agent_memory. The existing asynchronous worker can serve several conversations, with at most one active generation per conversation and a configurable process-wide limit.

## Phases

1. [Text chat and history](phase-01-text-chat.md) — CRUD, authorization, checkpoint history, and streaming.
2. [Microphone and photo input](phase-02-inputs.md) — editable transcripts and authorized photo attachments.
3. [Retrieval tool](phase-03-retrieval-tool.md) — controlled integration first, then real search and citations.
4. [Inquiry draft](phase-04-inquiry-draft.md) — editable draft from saved chat and evidence.
5. [Integration and verification](phase-05-verification.md) — native and container checks and product smoke tests.

Phase 1 provides the base for phases 2–4. Phase 2 needs the speech and files contracts for live use. Phase 3 needs the teammate’s search and evidence contracts for live use. Phase 4 can generate a text-only draft after phase 1, then include citations once phase 3 is integrated. Submission is owned by the inquiry API. Phase 5 verifies the combined behavior once the live dependencies are available.

## Implementation rules

- Follow [project guidance](../../AGENTS.md), [engineering standards](../../docs/development/code-standards.md), and [agent development](../../agent/docs/development.md). File additions and endpoints in the phases are proposed. Code sketches illustrate boundaries.
- Keep the standalone package independent and use its existing async ModelArk client and checkpoint setup. Check the exact model’s image and tool support before live integration. Controlled providers are the default for tests.
- Use existing app ORM models and migrations. History adds an HTTP response type and reads existing checkpoints. Revisit storage only if a concrete requirement needs a schema change.
- Read whole checkpoint state initially and bound the public response. Measure history cost before adding a new storage layer. Reassess conversation admission across instances before deploying more than one worker.
- Treat source material as evidence, preserve provenance and uncertainty, and keep model reasoning and service credentials private.
