# System responsibilities

**Status:** intended service boundaries, with current implementation noted below.
This guide defines who handles each task; it does not claim all integrations exist.

## Service boundaries

| Service | Intended responsibility |
|---|---|
| Frontend | Employee/expert screens, voice/photo input, answer display, inquiry review |
| Backend | User login, ownership checks, application records, knowledge processing/search, inquiry workflow |
| Agent | Generate answers and inquiry drafts through an API; preserve agent conversation state |
| OCR | Read text from supported images/documents; OCR means optical character recognition |
| Speech-to-text | Convert employee speech to text |
| Text-to-speech | Convert supported responses to spoken audio |
| PostgreSQL | Application records, searchable knowledge, and agent memory in separate databases |

Use Docker and independently runnable services. Kubernetes is not planned.
New services must support native development without repeated image builds; follow
[engineering standards](../development/code-standards.md) rather than copying legacy structure.

## Data responsibilities

- One PostgreSQL container hosts the proposed backend database and existing `agent_memory`.
- The backend owns users, machines, files, knowledge, conversation ownership, and inquiries.
- The agent owns LangGraph messages and checkpoints: saved state used to continue answering.
- Files stay outside PostgreSQL; records contain their location and permissions.
- Backend-to-agent calls use a service secret, separate from user login and provider keys.
- The backend checks user permissions before calling the agent. An identifier alone
  is not proof that a caller may access a conversation or inquiry.

The [database proposal](database-schema.md) is the source for target tables and
relationships. Existing per-collection databases remain until deliberately migrated.

## Employee assistance

- The frontend sends user requests through the backend.
- The backend checks ownership and calls the agent's answer API.
- Retrieval supplies relevant extracted knowledge and evidence; its integration
  with the standalone agent is still to be implemented. The tool interface is not fixed here.
- The agent streams progress and returns `complete.answer` only after generation
  and final checkpoint saving succeed. Clients replace provisional text with that answer.
- Missing completion remains unfinished to the caller, even if a disconnect hid
  a successfully saved answer. Current APIs do not guarantee replay or duplicate prevention.

## History and inquiries

- The backend lists conversations from its own records.
- Past messages would be read through a new authenticated agent history API,
  after backend ownership checks. This API does not yet exist.
- Initial history access loads saved conversation state; message-by-message paging
  is not provided. Measure cost before adding another message store.
- Inquiry Creation saves a self-contained account of the situation in the backend.
- Expert decisions and follow-up exchanges are preserved separately from agent history.
- An expert can read a submitted inquiry without the agent or whole conversation.
- An accepted inquiry does not automatically enter knowledge extraction.

## Current implementation boundaries

| Component | Current boundary |
|---|---|
| Standalone `agent/` | Native CLI and Docker, ModelArk answering, streaming, PostgreSQL checkpoints |
| Standalone-agent tools/retrieval | Not implemented in its initial service |
| Older backend agent | Separate implementation; do not confuse its in-memory history with standalone-agent memory |
| Backend knowledge/search | Existing per-collection databases and two search representations; not the target schema |
| OCR, voice, images, inquiry flow | Some earlier support exists; the full intended flow is not established |
| 3D assistance | Undecided; no selected generation or visualization approach |

Use the [codebase map](../development/codebase-summary.md) to locate implementation,
and service guides for runnable commands. Older HTML diagrams remain reference material.
