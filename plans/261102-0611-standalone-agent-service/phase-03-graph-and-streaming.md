# Phase 3: Graph, provider and streaming

Priority: P1. Status: completed. Estimate: 10 engineering hours. Dependency: Phase 2.

## Context and outcome

[Technical design](technical-design.md), [test plan](test-plan.md), [runtime research](reports/runtime-research.md). Implement a single model node with direct AsyncOpenAI streaming. Public completion requires successful graph exhaustion; the real-saver proof follows in Phase 4.

## Planned files

| Action | Absolute target | Responsibility |
|---|---|---|
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/clients/modelark.py` | Direct SDK configuration and standard-content stream |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/runtime/graph.py` | MessagesState graph, context selection and model node |
| Modify | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/runtime/conversation.py` | Request execution, deadline and cancellation |
| Modify | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/app.py` | Provider resource lifecycle |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/unit/test_graph.py` | Finish reasons, bounds, content filtering and cleanup |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/integration/test_provider_stream.py` | Actual SDK wire request and deterministic chunks |

## Test-first steps

1. Write provider-wire tests against a deterministic HTTP stub. Assert explicit endpoint/model/key, retries zero, Chat Completions, stream true and `max_tokens` without adapter rewriting.
2. Write node tests for ordinary content, private-reasoning exclusion, nonempty stop and length/filter/empty failures. Test `MAX_ANSWER_CHARACTERS = 32_000` below, at and beyond the boundary, including fragmented text. Observe intended failures.
3. Implement one-node graph. Use `get_stream_writer()` for custom deltas; append one AIMessage only after successful stop.
4. Consume `graph.astream` using custom v2 output and sync durability. Exhaust the iterator before public complete. Keep public event mapping independent.
5. Write real-TCP first-delta barrier and blocked-provider cancellation tests before implementing stream lifetime/deadline handling.
6. Verify bounded recent context without persisted-history mutation or repeated saved system prompts. Run targeted tests and refactor.

## Checklist and acceptance

- [x] Actual HTTP stub proves field compatibility and incremental content.
- [x] Only answer content reaches SSE. Length/filter/empty/missing-finish failures never complete.
- [x] Timeout/disconnect closes upstream stream and releases request admission.
- [x] No automatic provider retry follows partial text.
- [x] Graph, SDK and stream signatures match locked versions.
- [x] Model prompt claims no factory evidence or tools. Context bounds make no exact token guarantee.

## Risk and next step

ModelArk models differ in context and output budgets. Initial 120-second/1024-token values are configurable demo defaults. Validate the selected live model only through explicit opt-in. Continue to [Phase 4](phase-04-persistent-memory.md) for actual checkpoint and restart proof.

## Completion evidence

Completed on 2026-11-02. The final combined suite passed all 63 tests. Formatting, lint, collection and repository checks passed. See [implementation verification](reports/implementation-verification.md) and the scoped reports it references.
