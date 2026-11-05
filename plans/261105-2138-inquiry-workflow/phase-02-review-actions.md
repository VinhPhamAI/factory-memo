# Phase 02 — Review actions and timeline

## Context links

- [Plan overview](plan.md), [phase 01](phase-01-schema-and-crud.md)
- ORM: `InquiryEvent`, `InquiryEventKind`, `EVENT_KINDS_REQUIRING_CONTENT`
  in `backend/src/storage/orm/inquiries.py`
- Row-lock examples: `backend/src/storage/auth.py:306`, `backend/src/storage/repositories/lifecycle.py:28`
- Docs: `docs/architecture/database-schema.md` §10 (event + status in one transaction, row lock)

## Overview

- Priority: P0.
- Status: completed (2026-11-05). Nothing committed.
- Adds accept, edit, deny, request clarification and reply, with the timeline.

## Key insights

- Event kinds and the content rule already exist in the schema; no migration is needed.
- `inquiry_events` rejects UPDATE/DELETE by trigger, so events are permanent.
- The database docs require the event and the status change in one transaction
  with the inquiry row locked.

## Requirements

Functional:
- `POST /v1/inquiries/{id}/events {kind, content?}` with `kind` in
  `accepted`, `edited`, `denied`, `question`, `reply`.
- Allowed transitions are exactly the table in [plan.md](plan.md). Any other
  status, kind or actor returns `409 invalid_transition` (a user who cannot open
  the inquiry still gets `404`).
- `edited`, `denied`, `question`, `reply` need non-empty content (`422` otherwise).
- `GET /v1/inquiries/{id}` returns events oldest first with `{id, kind, content,
  actor: {id, display_name}, created_at}`.

Non-functional:
- Two concurrent actions on one inquiry: exactly one succeeds; the other gets `409`.
- `closed` (event kind) is not exposed in this phase.

## Architecture

```python
# backend/src/services/inquiries.py
TRANSITIONS: dict[tuple[str, str], tuple[str, str]] = {
    # (current status, event kind): (actor role in inquiry, next status)
    ("open", "accepted"): ("assignee", "answered"),
    ("open", "edited"): ("assignee", "answered"),
    ("open", "denied"): ("assignee", "closed"),
    ("open", "question"): ("assignee", "needs_info"),
    ("needs_info", "reply"): ("requester", "open"),
}
```

`InquiryStore.record_event(inquiry_id, actor_id, kind, content)`:
1. `SELECT … FROM inquiries WHERE id = :id AND deleted_at IS NULL FOR UPDATE`.
2. Check visibility, then `TRANSITIONS[(status, kind)]` and the actor's role.
3. Insert the event, update `status`, commit. Return the event.

## Related code files

Modify: `backend/src/services/inquiries.py`, `backend/src/storage/inquiries.py`,
`backend/api/inquiries.py`, `backend/tests/test_inquiries.py`.

## Implementation steps

1. Write failing tests: each allowed transition; each disallowed pair (for example
   accept from `needs_info`, reply from `open`, requester accepting, assignee replying);
   missing content; unrelated user `404`; concurrency (two threads, one `409`).
2. Add `TRANSITIONS` and `record_event` with the row lock.
3. Add the route and the event list in the detail response.

## Todo list

- [x] Failing transition tests
- [x] Locked `record_event`
- [x] Route + timeline in detail response
- [x] Tests green

## Success criteria

- The full matrix of status × kind × actor matches the table; only successful
  actions create `inquiry_events` rows.
- Draft → submit → question → reply → accept yields events
  `question, reply, accepted` in order and final status `answered`.

## Risk assessment

- Lock timeout (5 s, `app_database.py:59`) under contention returns `503`; acceptable at this scale.

## Security considerations

- Actor role is derived from the inquiry row, never from the request body.
- Content is stored as plain text and rendered as text in the UI (no HTML).

## Next steps

Phase 4 uses this endpoint; phase 5 runs the full sequence end to end.
