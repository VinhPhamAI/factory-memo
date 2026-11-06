# Phase 03 agent half report

Status: completed (agent only; backend/frontend/docs untouched, no commit).

Files (all under agent/):
- new: src/denso_agent/schemas/inquiry_draft.py (InquiryDraftFields, InquiryDraft, InquiryDraftError)
- new: src/denso_agent/runtime/inquiry_draft.py (prompt, create_draft, evidence dedupe)
- new: tests/unit/test_inquiry_draft.py (16 tests)
- mod: clients/modelark.py (DRAFT_TOOL, draft_inquiry, DRAFT_MAX_TOKENS=4096)
- mod: api/routes.py (route), runtime/conversation.py (optional `provider` arg), app.py (passes provider)

Contract: POST /v1/agent/conversations/{id}/inquiry-draft, service token, no body.
200 {fields:{title,situation,observations,attempted_actions,proposed_fix}, citations:[Citation], attachments:[{file_id,media_type}]}.
Citations dedupe by evidence_id, attachments by file_id, from history only.
Errors (envelope {error:{code,message}}): 401 unauthorized; 422 empty_conversation (no model call);
502 draft_failed (provider error/timeout, no tool call, bad JSON, invalid/over-long/unknown fields);
503 unavailable (runtime/provider missing or checkpoint read fails); 409 conversation_busy; 429 capacity_exceeded.
Busy/capacity: reuses Admission acquire/release like stream, so a draft cannot overlap an active chat turn and counts toward capacity; released in finally.
Model: agent's configured model; tool_choice pinned to write_inquiry_draft.

Tests: `cd agent && uv run pytest tests/unit -q` -> 113 passed (was 97 + 16 new). black applied; pylint clean on new files.

Open issues:
- Pinned tool_choice support by the served model (ModelArk / vLLM Qwen3.5-4B) is unverified; a rejection surfaces as 502 draft_failed. No JSON-prompt fallback.
- Backend must treat 409/429 from this route as retryable, and 502 draft_failed -> its own 502.
- Pre-existing history bounding applies, so very long chats are drafted from the newest messages only; evidence also comes only from that window.
