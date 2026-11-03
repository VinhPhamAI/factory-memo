# Phase 03 — Retrieval tool and citations

## Prerequisites

- The text conversation path persists answers and can reload them through the agent history API.
- Agree with the knowledge-base owner on authorization, search fields, evidence references, and errors before integrating a live client. Build the agent flow with controlled provider and search responses while this API is in development.
- Verify the configured ModelArk deployment supports tool calls before enabling live retrieval. Use controlled responses in normal tests and obtain explicit opt-in for any paid verification.

## Proposed changes

| File | Change |
|---|---|
| `agent/src/denso_agent/runtime/graph.py` | Give the model a single `search_knowledge` tool. Execute only validated calls, then continue generation using returned evidence. |
| `agent/src/denso_agent/clients/modelark.py` | Parse the provider's tool calls, including fragmented arguments. Keep tool-routing output private and stream the user-facing answer. |
| `agent/src/denso_agent/clients/retrieval.py` | Add the HTTP client after the knowledge API contract is agreed. Validate responses and set a request deadline. |
| `agent/src/denso_agent/runtime/conversation.py` | Handle intermediate graph updates and identify the final saved answer without accepting tool updates as completion. |
| `agent/src/denso_agent/schemas/events.py` and history response types | Carry the same public citation data in completion and history. |
| Conversation backend proxy and frontend client | Forward authorized scope on stream requests and render citations through permission-checked source routes. |

## Implementation

1. Define the model-visible argument as a bounded, nonempty `query`. Resolve authorized knowledge-base IDs on the backend from the user and conversation context. Pass scope through the authenticated service request. The model cannot select arbitrary KB IDs or URLs.
2. Agree on ranked results carrying stable evidence IDs, excerpts, and source locations. Agree on what the backend needs to recheck user access and open a source. Differentiate an empty result from an unavailable or failed search.
3. Set explicit limits on tool rounds, elapsed time, and result count and size. Reject malformed or unknown calls. Where tool support is enabled, keep tool results and intermediate messages out of public history. Treat retrieved text as evidence. Evidence content cannot issue instructions or broaden search scope.
4. Validate generated citation IDs against the returned evidence for that turn. Persist the public citation references with the completed answer in the agent checkpoint. Publish completion only after successful saving. Opening a citation still checks current source access.
5. Enable the live integration after validating the agreed API with an authorized fixture and verifying the configured model. Keep simulated results in tests.

Illustrative tool boundary. The caller builds `scope` from authenticated request context.

```python
async def search_knowledge(
    query: str, scope: SearchScope, client: RetrievalClient
) -> SearchResult:
    """Fetch ranked evidence within an authorized search scope.

    Args:
        query: Validated question supplied by the model.
        scope: Knowledge-base access resolved by the backend.
        client: Search client with configured time and result limits.

    Returns:
        Ranked evidence and its stable source references.

    Raises:
        RetrievalUnavailable: Search could not be completed.
    """
    return await client.search(query=query, scope=scope)
```

## Concrete call path

1. Edit `backend/api/conversations.py` and `backend/src/clients/agent.py` to resolve and forward trusted search scope. The knowledge API contract must specify how it authenticates the agent and validates this scope. Until that contract is agreed, use controlled dependencies in tests.
2. Edit `agent/src/denso_agent/clients/modelark.py` to add a routing call that returns either a validated tool request or a decision to answer directly. Keep routing output private. Use the existing streaming answer path for the final response. Start with one search invocation per turn and document the extra routing call and its latency.
3. Edit `agent/src/denso_agent/runtime/graph.py` to add a search branch. Feed the result into final generation and keep the existing final answer node as the completion boundary. Edit `runtime/conversation.py` to distinguish intermediate graph updates from the final answer update. Create `agent/src/denso_agent/clients/retrieval.py` for live HTTP only after the contract is agreed.
4. Edit `schemas/events.py` and `schemas/chat.py` to use a shared public citation shape. The graph persists the validated citation list on the final assistant message. Both completion and history project from that saved representation. Edit `frontend/src/services/conversations-api.ts` and `frontend/src/hooks/workspace.tsx` to carry citation data through to existing evidence rendering. Resolve evidence links via the backend with current access checks.

**First failing test.** Extend `agent/tests/unit/test_graph.py` with a controlled model that requests a tool and returns one valid and one invented citation ID. Assert that the search received the trusted scope, that only the valid reference becomes a link, and that public text comes from the final answer. Add a direct-answer case that makes zero search calls. Verify persistence and the no-completion-on-save-failure case in `agent/tests/integration/test_memory.py`.

## Test first and finish

- [ ] A controlled model issues a tool call and uses the returned evidence. Tool calls and output stay out of public history.
- [ ] Invalid arguments, unknown tools, timeouts, exhausted tool limits, and search failures produce bounded behavior. Empty results and failed requests are distinguishable.
- [ ] Scope comes from the backend even when the model tries to supply a different KB. Fabricated citation IDs never become links.
- [ ] Streaming still delivers answer fragments before completion. Failed checkpoint saves emit no successful completion.
- [ ] A known fault returns a clickable source on the live integration. The citation survives restart, and the source route rejects unauthorized readers.
