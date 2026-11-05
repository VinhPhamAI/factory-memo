# Phase 1 — Connect authorized KB reads

**Status** Implemented with controlled tests. **Depends on** existing login, conversation ownership, and KB routes.

| Action | Path | Change |
| --- | --- | --- |
| Modify | `backend/api/auth.py` | Reuse the existing bearer parser for chat forwarding and preserve `require_current_user` behavior. |
| Modify | `backend/api/conversations.py`, `backend/src/clients/agent.py` | After the ownership check, pass the current access token to `AgentClient.open_stream` in a separate internal header. |
| Modify | `agent/src/denso_agent/api/routes.py`, `agent/src/denso_agent/runtime/conversation.py` | Accept the employee credential on authenticated stream requests and pass it through request-local graph context. |
| Add | `agent/src/denso_agent/clients/knowledge.py` | Implement typed async calls to existing list and search routes. |
| Modify | `agent/src/denso_agent/app.py`, `agent/src/denso_agent/config.py` | Own the pooled HTTP client in the service lifespan. |
| Modify | `.env.example`, `agent/.env.example`, `deployments/agent.compose.yml` | Document and wire the forwarding toggle and callback address. |
| Test | `backend/tests/test_conversations.py`, `agent/tests/unit/test_api.py`, new `agent/tests/unit/test_knowledge_client.py`, `agent/tests/integration/test_memory.py` | Cover ownership, HTTP mapping, isolation, and credential handling. |

## Steps

1. **First failing test.** An owned chat forwards the employee credential only when enabled. A foreign chat or invalid session never calls the agent. Service authorization uses its existing token.
2. Add optional backend `AGENT_KB_TOOLS_ENABLED`, default false. Keep the service bearer in `Authorization`. Carry the employee's current access token in `X-Employee-Access-Token` on the internal stream request. Refresh credentials stay in the browser/backend flow.
3. Call `GET /v1/knowledge-bases` and `POST /v1/knowledge-management/{kb}/search` on the fixed `AGENT_KB_BACKEND_URL`. Set the bearer per request. Disable redirects and automatic retries. Validate KB names before building paths. Use HTTPS across untrusted networks.
4. Validate the response body. Treat a successful empty response separately from expired credentials, denied access, a removed KB, malformed data, or backend unavailability. Keep raw response bodies and credentials out of logs and public events.
5. Carry a redacted credential through `StateGraph(..., context_schema=...)` and `graph.astream(..., context=...)`. Keep it out of graph state, `configurable`, checkpoint metadata, model inputs, traces, and exceptions. Use a unique credential sentinel in a real checkpoint test.
6. Document `http://127.0.0.1:8005` for native services and `http://api:8005` for paired Compose services. The callback URL defaults to unset. Without both a client and a credential, standalone chat runs without tools.

```python
# Sketch inside AgentClient.open_stream, after backend ownership validation.
headers = self._headers.copy()
if employee_access is not None:
    headers["X-Employee-Access-Token"] = employee_access.get_secret_value()
request = self._http.build_request(
    "POST", "/v1/agent/stream", headers=headers, json=payload
)
```

## Done when

- [x] Controlled tests exercise routes, per-user credentials, redirect rejection, and response bounds. Backend auth tests cover expiration and revocation.
- [ ] Tests verify the credential is absent from checkpoint state and metadata, history, stream frames, model inputs, and captured logs.
- [x] Controlled tests cover chat with tools disabled or no KB backend configured.
- [x] Update `agent/docs/api.md`, `agent/docs/development.md`, and `docs/features/conversations.md` with setup and the credential boundary.
