# Phase 03 — Draft from chat

## Context links

- [Plan overview](plan.md), [phase 01](phase-01-schema-and-crud.md)
- Superseded proposal: `plans/261103-1436-agent-conversations/phase-04-inquiry-draft.md`
- Agent: `agent/src/denso_agent/api/routes.py` (service-token routes),
  `runtime/conversation.py:152` (`load_history`), `clients/modelark.py:99`
  (`route()`: tool call + `json.loads`), `schemas/chat.py` (`HistoryMessage`),
  `schemas/citations.py` (`Citation`)
- Backend: `backend/src/clients/agent.py` (`fetch_history`), `backend/api/conversations.py`

## Overview

- Priority: P1.
- Status: completed (2026-11-05). Forced tool call not yet checked on the live model. Nothing committed.
- From a chat, generate an editable draft with situation, observations,
  attempted actions, proposed fix and evidence, saved as a `draft` inquiry.

## Key insights

- The agent has no structured-output method. `route()` already forces a tool call
  and parses arguments; reuse that pattern with a `write_inquiry_draft` tool and
  `tool_choice` pinned to it.
- History already carries citations `{evidence_id, title, excerpt, location}` and
  photo `attachment{file_id}`. These become evidence without another model call.
- Citations are empty until knowledge search is connected; the feature must work
  with zero citations.

## Requirements

Functional:
- Backend `POST /v1/conversations/{id}/inquiry-draft` (owner only) returns the
  created draft inquiry (`201`), identical in shape to `POST /v1/inquiries`.
- The model fills `title`, `situation`, `observations`, `attempted_actions`,
  `proposed_fix`. Unknown facts stay empty strings.
- Evidence is built by code, not by the model: every saved citation becomes a
  `citation` item (title, excerpt, location copied); every photo attachment the
  requester can still read becomes a `file` item plus an `inquiry_files` row.

Non-functional:
- Empty history → `422 empty_conversation`; model failure or invalid output →
  `502 draft_failed`; agent unreachable → `503`. No draft row is created on failure.
- An unauthorized request makes zero agent calls.
- Bounded input: reuse `history_max_messages` / `history_max_characters`.

## Architecture

```
Browser ──POST /v1/conversations/{id}/inquiry-draft──▶ Backend
  Backend: require_active_owner → AgentClient.create_inquiry_draft(id)
  Agent:   load_history → ModelArk.draft_inquiry(messages) → validate → DraftFields
  Backend: build evidence from history citations/attachments
           → InquiryService.create_draft(...) → 201 inquiry
```

<!-- Updated: Validation Session 1 - model choice and immediate save -->
Model: the agent's configured model (`ModelArk` client settings). No second
model configuration is added. The backend saves the `draft` row as soon as the
agent returns valid fields; there is no unsaved preview step.

Agent response:

```python
class InquiryDraftFields(BaseModel):
    """Model-written draft text; evidence is added by the backend."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=200)  # required key; may be ""; submit requires a title
    situation: str = Field(default="", max_length=4000)
    observations: str = Field(default="", max_length=4000)
    attempted_actions: str = Field(default="", max_length=4000)
    proposed_fix: str = Field(default="", max_length=4000)
```

The agent returns `{fields, citations, attachments}`; citations and attachments
come straight from the loaded history, so the backend needs one agent call.

Prompt rules: separate reported observations, actions actually performed and
suggested next steps; leave a field empty when the chat does not state it; never
include reasoning text.

## Related code files

Create: `agent/src/denso_agent/schemas/inquiry_draft.py`,
`agent/src/denso_agent/runtime/inquiry_draft.py`,
`agent/tests/unit/test_inquiry_draft.py`.
Modify: `agent/src/denso_agent/clients/modelark.py` (`draft_inquiry`),
`agent/src/denso_agent/api/routes.py` (`POST /v1/agent/conversations/{id}/inquiry-draft`),
`backend/src/clients/agent.py` (`create_inquiry_draft`),
`backend/api/conversations.py` (route), `backend/tests/test_conversations.py`.

## Implementation steps

1. Agent tests first with a controlled provider: valid draft; empty history;
   malformed tool arguments; over-long field; provider timeout.
2. Implement schema, `ModelArk.draft_inquiry` (forced tool call), runtime
   `create_inquiry_draft`, route with service-token auth.
3. Backend tests first with the existing `ControlledAgent` stub: non-owner gets
   `404` with zero agent calls; success creates a draft with copied evidence;
   agent failure creates no row.
4. Implement `AgentClient.create_inquiry_draft` and the backend route.

## Todo list

- [x] Agent unit tests
- [x] Agent schema, client method, runtime, route
- [x] Backend tests
- [x] Backend client + route

## Success criteria

- The owner gets a saved, editable draft from a real chat; evidence matches the
  history's citations and readable photos exactly.
- Failures leave chat history and inquiry tables unchanged.
- A draft stays readable after the conversation is deleted or the agent is stopped.

## Risk assessment

- The served model (ModelArk or vLLM Qwen3.5-4B) may not honour forced tool calls.
  Check once against the configured model; fallback is a JSON-only prompt parsed
  with the same Pydantic model.
- Small models may invent actions. Mitigation: prompt rule + the employee edits
  before submit; the draft is never sent automatically.

## Security considerations

- Agent route accepts only the service token; ownership is checked by the backend first.
- Photo file IDs from history are re-checked against the requester's readable files
  before they are attached.
- Chat text is treated as data in the prompt, not instructions.

## Next steps

Phase 4 adds the chat button and draft editor.
