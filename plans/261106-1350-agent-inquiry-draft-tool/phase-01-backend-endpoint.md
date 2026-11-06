# Phase 01 — Backend endpoint for agent drafts

Priority: high. Status: superseded.

<!-- Updated: Validation Session 1 - phase dropped -->
Superseded by validation session 1: the agent uses the existing
`POST /v1/inquiries` and `PATCH /v1/inquiries/{id}`. No backend change. Kept for history.

## Context

- `backend/api/conversations.py` draft route: calls the agent, then
  `InquiryService.create_draft_from_agent`.
- `backend/src/clients/agent.py`: `AgentInquiryDraft`, `DraftFields`, `Citation`, `AttachmentRef`.
- `backend/api/inquiries.py`: router, `rule_violation`, `inquiry_response`.

## Requirements

- New route `POST /v1/inquiries/agent-drafts`, authenticated like every inquiry route.
- Body: `AgentInquiryDraft` fields plus `conversation_id` (`extra="forbid"`).
- Calls `service.create_draft_from_agent(user, conversation_id, draft)` unchanged.
- Same error mapping as the conversation draft route:
  `ConversationNotFound` → 404, `InvalidAgentDraft` / `UnreadableFile` → 422.
- No new trust: `POST /v1/inquiries` already accepts citation evidence from the caller.

## Files

- Modify `backend/api/inquiries.py` (route + request model).
- Modify `backend/tests/test_inquiries.py` (or `test_inquiry_draft_route.py`).

## Steps

1. Add `AgentDraftRequest(AgentInquiryDraft)` with `conversation_id: UUID`.
2. Add the route; return `inquiry_response(detail)` with `201`.
3. Tests: success creates `draft` with evidence; foreign conversation → 404;
   oversized field → 422; unreadable photo is skipped.

## Success criteria

- Targeted tests pass: `uv run python -m unittest backend.tests.test_inquiries -v`.
