---
title: "Full answer in the final streaming event"
status: completed
created: 2026-11-02
---

# Objective

Add required `complete.answer` to the existing streaming API. The caller can
display text fragments and replace them with the full saved answer on completion.
The user approved this plan in the chat.

## Implementation decisions

- Retain the existing endpoint, event names, credentials and database schema.
- Extend `CompleteEvent` with a required answer string.
- Consume LangGraph custom text and node updates together. Read one nonempty
  answer from the current answer node's completed assistant message.
- Do not reconstruct the final answer from fragments or perform an extra database
  read. Missing, malformed or ambiguous final output produces `persistence_error`.
- Send completion only after graph exhaustion and synchronous checkpoint saving
  succeed. Preserve the answer limit, deadlines, cancellation and admission cleanup.
- Add no model changes, migration, retry, replay or answer-retrieval endpoint.

## Tasks

| Task | Status | Evidence |
|---|---|---|
| Write tests and observe intended failures | Complete | API tests failed before implementation: 11 failed, 16 passed, exit 1 |
| Update completion schema and stream handling | Complete | Targeted API suite passed after implementation; independent review found no issues |
| Update unit, PostgreSQL and native/container assertions | Complete | Full suite passed with real PostgreSQL, native CLI and container checks |
| Update API, development and README documentation | Complete | Documentation includes answer replacement, compatibility and disconnect limits |
| Run full suite, formatting, lint and independent review | Complete | Final combined suite: 75 passed in 124.77 seconds; formatting, lint and independent review passed |

## Acceptance

- The completion contains the full assistant answer, including Unicode and JSON
  escaping, independently of provisional fragments processed by the caller.
- A real saved assistant message equals `complete.answer`.
- Provider failures, timeouts, cancellation, invalid graph output and failed final
  saving produce no successful completion.
- Existing incremental streaming, process/container restart, reload and cleanup
  checks remain valid.
- Documentation explains replacement rather than appending, missing completion,
  strict client validation updates and the absence of disconnect recovery.

Actual results and review findings belong in [verification](verification.md).

All tasks are complete. The final frozen-package run passed 75 tests without
changing production code, tests or deadlines after the first combined-run
startup failure. That failure and the unchanged successful reruns remain in the
verification record.
