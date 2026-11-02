# Phase 2: API contracts and access

Priority: P1. Status: completed. Estimate: 6 engineering hours. Dependency: Phase 1.

## Context and outcome

[Plan](plan.md), [technical design](technical-design.md), [test plan](test-plan.md). Expose typed HTTP/SSE contracts with a shared-secret trust boundary. Conversation ownership remains the backend's responsibility.

## Planned files

| Action | Absolute target | Responsibility |
|---|---|---|
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/app.py` | Factory and lifecycle injection |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/api/routes.py` | Thin stream and health/readiness handlers |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/api/authentication.py` | One constant-time service-token check |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/api/sse.py` | Typed event framing and safe error mapping |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/schemas/chat.py` | Request and JSON error shapes |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/schemas/events.py` | Public event shapes |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/tests/unit/test_api.py` | Auth, validation, framing and health tests |
| Create | `D:/HACKATHONS/DENSO/denso-hackathon/agent/src/denso_agent/runtime/conversation.py` | Bounded active-request admission and release |

## Test-first steps

1. Write unauthorized, validation-boundary, body-cap and endpoint-policy tests with in-process fake runtime resources. Assert provider/checkpoint counters remain zero on rejection.
2. Observe intended failures. Implement schemas, centralized authentication and thin routes. Normalize prestream JSON errors.
3. Write overlapping-conversation/global-capacity tests and error/disconnect release tests. Implement active-ID tracking bounded by admitted concurrency.
4. Specify typed status/text/complete/error frames. Public schemas must not depend on internal LangGraph event structures.
5. Run unit suite. Confirm UUID-only conversation IDs, trimmed 1..20000 text, unknown-field rejection and chunked-body limit.

## Checklist and acceptance

- [x] Missing/invalid credentials reject before provider/checkpoint access.
- [x] Health is public and minimal; readiness is protected and makes no model call.
- [x] Prestream errors have the selected JSON envelope and HTTP status.
- [x] Same-conversation overlap returns 409; global exhaustion returns 429 before stream headers.
- [x] Admission state is released on every path and cannot grow with inactive conversations.
- [x] Server request IDs provide tracing only. No idempotency, replay or user-auth claim appears.

## Risk and next step

Streaming begins after admission, so later errors cannot change HTTP status. Terminal delivery is best effort after connection loss. One process/instance is required. Continue to [Phase 3](phase-03-graph-and-streaming.md) after the contract suite passes.

## Completion evidence

Completed on 2026-11-02. The final combined suite passed all 63 tests. Formatting, lint, collection and repository checks passed. See [implementation verification](reports/implementation-verification.md) and the scoped reports it references.
