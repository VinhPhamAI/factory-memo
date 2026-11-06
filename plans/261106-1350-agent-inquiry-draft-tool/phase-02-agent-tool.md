# Phase 02 — Agent tools `create_inquiry_draft` and `update_inquiry_draft`

Priority: high. Status: completed (code, unit tests, docs) 2026-11-06. Live-model check passed 2026-11-06.

<!-- Updated: Implementation 2026-11-06 - update has no inquiry_id argument, conditional offer, conditional prompt -->

## Context

- `agent/src/denso_agent/runtime/knowledge.py`: `KNOWLEDGE_TOOLS`, `run_knowledge`, `KnowledgeTurn`.
- `agent/src/denso_agent/runtime/inquiry_tools.py` (new): inquiry tool offer, routing and persistence.
- `agent/src/denso_agent/runtime/inquiry_draft.py`: `create_draft(provider, history, focus)`.
- `agent/src/denso_agent/runtime/history.py` (new): saved LangGraph messages to `ChatHistory`,
  shared by `conversation.py` and the tools.
- `agent/src/denso_agent/clients/backend.py` (new): bounded backend request helper shared by
  the knowledge and inquiry clients.
- `agent/src/denso_agent/clients/inquiries.py` (new): `InquiryClient` (`create`, `get`, `update`).
- `agent/src/denso_agent/schemas/tools.py`: `ToolName`, `ToolState`, `ToolActivity`.
- Backend contract: `backend/api/inquiries.py` (`CreateInquiryRequest`, `UpdateInquiryRequest`),
  limits and mapping in `backend/src/services/inquiries.py` (`EvidenceItem`, `InquiryContext`,
  `create_draft_from_agent`, `MAX_EVIDENCE=50`, `MAX_EVIDENCE_ID=200`, `MAX_DRAFT_FILES=20`).

## Tools

Offered in the existing `decide_tool` call, only when an employee credential and a UUID
conversation ID are present.

| Tool | Arguments | Effect |
|---|---|---|
| `create_inquiry_draft` | `focus?: str` (≤500) | Create a new draft, or ask first if the chat already has one. |
| `update_inquiry_draft` | optional `title`, `situation`, `observations`, `attempted_actions`, `proposed_fix` | Replace only the given fields of the chat's latest agent-created draft. |

The update tool has no `inquiry_id` argument. The model cannot see saved IDs in later turns,
and the tool never accepts a foreign ID. It always targets the latest agent-created draft.

## Behavior

1. Prior drafts are the `inquiry_id`s of earlier create results in the checkpointed AI
   messages, read through `history.py`. No backend query for this.
2. `update_inquiry_draft` is offered while the latest agent-created inquiry is still a
   draft. Each turn of such a conversation runs one `GET` before routing.
   <!-- Updated: live check 2026-11-06 - the turn-after-result gate blocked a real edit:
   the model asked edit-vs-new itself without a tool call, so the next turn had no inquiry
   result and update was not offered. -->
3. `create_inquiry_draft`, no prior draft → create immediately.
4. `create_inquiry_draft`, prior draft, no `focus` → create nothing. Lazily `GET` the latest
   prior draft and return `needs_choice` with its title, status and fields. The answer asks
   whether to edit that draft or create a new one, and what exactly to change.
5. `create_inquiry_draft` with `focus` → create a new draft. `focus` goes into the draft
   prompt as "the inquiry covers: …", inside the data block.
6. `update_inquiry_draft` → `GET` the latest agent-created draft, replace only supplied fields,
   `PATCH` title and context with evidence unchanged. Backend `409` → `not_draft`.
7. Update not offered on this turn → inquiry activity `unavailable`; search is `not_used`.
8. A draft already submitted (not `draft` status) is not offered for editing. A repeated
   create then saves a new draft.
9. One inquiry tool call per turn; it ends tool routing for the turn.
10. Model, validation, backend or timeout failures → `unavailable`; the turn still answers.
11. `INQUIRY_INSTRUCTION` is appended to the system prompt only when the tools are offered.
    Rules: drafts only on explicit request; never claim the inquiry was sent; it still needs
    an expert and Submit on the inquiry page.

## Deviations from the original design

- (a) Update takes no `inquiry_id`. It targets the latest agent-created draft. The planned
  "foreign `inquiry_id` rejected" test is replaced by the "submitted draft is not offered" test.
- (b) Update is offered whenever the latest agent-created inquiry is still a draft (the
  earlier turn-after-result gate was removed after the live check). Ask-before-acting for a
  repeated create is enforced by `needs_choice`; edits rely on the tool description and prompt.
- (c) Prompt rules are conditional (item 11), not always on.
- (d) Unoffered update → `unavailable`, not a failed search.

## Create mapping (mirror `create_draft_from_agent`)

- `title` ≤200.
- Citations → `{"kind": "citation", "title": t[:300], "excerpt": e[:4000] or None, "location": l[:300], "evidence_id": id}`; skip IDs longer than 200.
- Photos from history and the current message → `file_ids` (≤20, deduplicated) and
  `{"kind": "file", "title": "Ảnh đính kèm", "file_id": id}`. Photos first; citations fill up to 50.
- Two differences, documented in a comment in `inquiry_tools.py`: the photo title is fixed
  (the agent does not know file names), and an unreadable photo (`422`) triggers one retry
  without any photos, where the backend skips only that photo.
- Backend `422` → retry once without photos; then `unavailable`.

## Files

- Created: `runtime/inquiry_tools.py`, `runtime/history.py`, `clients/backend.py`,
  `clients/inquiries.py`, `agent/tests/unit/test_inquiry_tool.py`.
- Modified: `runtime/knowledge.py`, `runtime/conversation.py` (history mapping moved to
  `history.py`), `runtime/graph.py` (conversation ID from `thread_id`, current history),
  `runtime/inquiry_draft.py` (`focus`), `runtime/prompts.py`, `schemas/tools.py`, `app.py`.
- `schemas/tools.py`: `ToolName` adds both tools; `ToolState` adds `needs_choice`, `not_draft`;
  `ToolActivity` adds `inquiry_id: UUID | None` and `title` (≤200). History keeps those two
  fields, never field text.

## Tests

`agent/tests/unit/test_inquiry_tool.py` (18 tests):

- [x] First create posts mapped fields, evidence and `file_ids`; activity `results` with `inquiry_id`.
- [x] Second request without `focus` → `needs_choice`, no POST.
- [x] Create with `focus` → POST; prompt contains the focus.
- [x] Update replaces only given fields and keeps evidence.
- [x] Update of a submitted inquiry → `not_draft` (`409`).
- [x] Submitted draft not offered for editing; a new one is saved.
- [x] Unreadable photo `422` → one retry without photos.
- [x] Failures and invalid calls still answer.
- [x] No credential → inquiry tools not offered.
- [x] Repeated request after an unrelated turn is asked about first.
- [x] Unrelated turn without a draft makes no backend request.
- [x] Deleted draft does not block a new one.
- [x] Unoffered update → `unavailable`, search `not_used`.
- [x] Non-UUID thread offers no inquiry tools or instructions.
- [x] Inquiry instructions sent only when tools are offered.
- [x] Saved history keeps `inquiry_id` and `title`.
- [ ] Foreign `inquiry_id` rejected: removed by deviation (a).

## Todo

- [x] Schemas, clients and shared backend helper.
- [x] Tools, routing, history mapping, prompt and draft focus.
- [x] Unit tests (agent unit 147 passed; agent e2e 5 passed).
- [x] Code review; fixes applied (M2, M3, M4, L1, L2, L3, L5, L6, L7). Open items in `plan.md`.
- [x] Lint and type checks: black clean; pylint 10.00/10.
- [x] Live-model check (paid, once). See `plan.md`.

## Success criteria

- [x] Agent unit tests pass per `agent/docs/development.md`.
- [x] Live check (paid, once): backend, worker and agent running; ask to send to an expert
  → draft in `/inquiries`; ask again → agent asks edit-vs-new; ask to change observations →
  PATCH applied.

## Live check

Passed 2026-11-06 via the backend API; details in `plan.md`.
