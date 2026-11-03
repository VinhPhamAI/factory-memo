# Phase 04 — Inquiry draft from chat

## Prerequisites

- The backend can authorize the conversation owner and the agent can read saved messages.
- Agree with the inquiry API owner on the draft payload and how a confirmed draft becomes an inquiry. Implement drafting against controlled dependencies while the submission API is in development.
- Drafting can start from text chat. Source citations become available as Phase 03 lands.

## Proposed changes

| File | Change |
|---|---|
| `agent/src/denso_agent/api/routes.py` | Add a service-authenticated draft endpoint keyed by conversation UUID. |
| `agent/src/denso_agent/schemas/inquiry_draft.py` | Define the draft fields and evidence references, with explicit size limits. |
| `agent/src/denso_agent/runtime/inquiry_draft.py` | Load a bounded snapshot of saved history and ask the provider for structured output. Validate the draft before returning it. |
| Backend conversation API and agent client | Gate drafting by conversation ownership and proxy the request with the agent service token. |
| Frontend conversation workspace | Add a draft action and editable preview. Hand off to the inquiry workflow only after user confirmation. |

## Implementation

1. Propose `POST /v1/agent/conversations/{id}/inquiry-draft` as the internal endpoint. Read the saved checkpoint and select completed public context for the draft. Define an explicit empty-history error and return sanitized failures for invalid model output, timeout, and unavailable storage. Keep generation bounded.
2. Return `situation`, `observations`, `attempted_actions`, `proposed_fix`, and `evidence`. Distinguish reported observations, attempted actions, and suggested next actions. Leave unsupported fields empty or identify them as unknown. Accept only evidence IDs already present in the snapshot and allowed for the user. Validate again at submission.
3. Keep drafting read-only with respect to chat and inquiry records. The employee edits and confirms the draft before the backend submission workflow persists a standalone inquiry snapshot and authorized evidence. An expert can read the saved inquiry with the agent offline.

Illustrative runtime boundary. The route authenticates the service and the backend authorizes ownership before invoking it.

```python
async def create_inquiry_draft(
    conversation_id: UUID,
    history: ConversationHistory,
    provider: DraftProvider,
) -> InquiryDraft:
    """Create an editable inquiry draft from a bounded saved chat snapshot.

    Args:
        conversation_id: Conversation owned by the authenticated user.
        history: Reader for saved public conversation messages and citations.
        provider: Client that generates structured draft fields.

    Returns:
        A validated draft with supported evidence references.

    Raises:
        EmptyConversation: The snapshot has no usable context.
        InvalidDraft: Model output violates the draft or evidence contract.
    """
    snapshot = await history.draft_snapshot(conversation_id)
    proposed = await provider.generate_draft(snapshot.messages)
    return validate_draft(proposed, snapshot.evidence_ids)
```

## Concrete call path

- Edit `backend/api/conversations.py` and `backend/src/clients/agent.py` to add the owner-gated draft route and `AgentClient.create_inquiry_draft`. Reuse the same active-owner query as history.
- Create `agent/src/denso_agent/schemas/inquiry_draft.py` with `InquiryDraft` and bounded fields. Create `runtime/inquiry_draft.py` with `create_inquiry_draft` and `validate_draft`. Reuse checkpoint loading from `runtime/conversation.py` and add `generate_draft` to `clients/modelark.py` for a bounded structured response. Register the endpoint in `api/routes.py`.
- Edit `frontend/src/services/conversations-api.ts` and `frontend/src/hooks/workspace.tsx` to request a draft for the selected chat and hold editable draft state separately from messages. Hand off only the confirmed form to the inquiry API.

**First failing tests.** Extend `backend/tests/test_conversations.py` to assert an unauthorized draft request makes zero agent calls. Create `agent/tests/unit/test_inquiry_draft.py` with a controlled provider returning a fabricated evidence ID and assert a validation failure. Add a workspace test that cancelling the preview sends no inquiry submission.

## Test first and finish

- [ ] The owner can draft from saved chat and edit the result. An unrelated user or a deleted conversation fails at the backend before the agent is called.
- [ ] Empty history, invalid fields, fabricated references, and provider failures produce clear errors. Missing observations or attempted actions are not invented.
- [ ] Generating or cancelling a draft leaves chat and inquiry records unchanged. Confirmation hands the edited draft to the inquiry workflow.
- [ ] Once the inquiry API is available, submit a draft and verify the assigned expert can read the saved snapshot with the agent offline.
