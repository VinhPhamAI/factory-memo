"""Specify access, bounded requests and public streaming behavior."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from denso_agent.api.body_limit import MAX_BODY_BYTES
from denso_agent.app import create_app
from denso_agent.config import RuntimeSettings
from denso_agent.runtime.conversation import AgentRuntime
from denso_agent.schemas.events import CompleteEvent
from tests.support.events import parse_events


class FakeGraph:
    """Count accepted generation calls and optionally hold a response open."""

    def __init__(self):
        """Initialize observable request and cleanup counters."""
        self.calls = 0
        self.closed = 0
        self.release = None
        self.fragments = ["answer"]
        self.final_updates = [{"answer": {"messages": [AIMessage(content="answer")]}}]
        self.fail_after_update = False
        self.save_release = None
        self.save_waiting = asyncio.Event()

    async def astream(self, *args, **kwargs):
        """Emit provisional fragments and a separate completed node result."""
        self.calls += 1
        self.args, self.kwargs = args, kwargs
        try:
            for fragment in self.fragments:
                yield {"type": "custom", "data": {"delta": fragment}}
            if self.release:
                await self.release.wait()
            for update in self.final_updates:
                yield {"type": "updates", "data": update}
            if self.save_release is not None:
                self.save_waiting.set()
                await self.save_release.wait()
            if self.fail_after_update:
                raise RuntimeError("Controlled final checkpoint failure")
        finally:
            self.closed += 1


@pytest.fixture
def settings():
    """Supply nonproduction credentials and ordinary runtime limits."""
    return RuntimeSettings(
        api_token="secret",
        api_key="provider-key",
        base_url="http://provider.test/v1",
        model="test",
        database_url="postgresql://runtime:pass@localhost/agent_memory",
    )


@pytest.fixture
def runtime(settings):
    """Build an in-process graph whose calls can be inspected."""

    async def ready():
        """Report a healthy fake database without network access."""
        return True

    return AgentRuntime(FakeGraph(), ready, settings)


@pytest.fixture
async def client(settings, runtime):
    """Call the API with injected resources instead of opening connections."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(settings=settings, runtime=runtime)
        ),
        base_url="http://agent.test",
    ) as connection:
        yield connection


def payload(message="hello"):
    """Create a fresh conversation request for independent test cases."""
    return {"conversation_id": str(uuid4()), "message": message}


def test_openapi_describes_stream_and_input(settings, runtime):
    """Generated API documentation exposes SSE and strict request schemas."""
    schema = create_app(settings=settings, runtime=runtime).openapi()
    operation = schema["paths"]["/v1/agent/stream"]["post"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
    assert "application/json" not in operation["responses"]["200"]["content"]
    assert (
        schema["components"]["schemas"]["ChatRequest"]["additionalProperties"] is False
    )


async def test_auth_rejects_before_graph(client, runtime):
    """Missing and incorrect service tokens cannot invoke generation."""
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = await client.post(
            "/v1/agent/stream", json=payload(), headers=headers
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
    assert runtime.graph.calls == 0


async def test_health_and_readiness(client):
    """Health is public; readiness requires the service secret."""
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 401
    assert (
        await client.get("/readyz", headers={"Authorization": "Bearer secret"})
    ).json() == {"status": "ready"}


@pytest.mark.parametrize(
    "body",
    [
        payload(" "),
        payload("x" * 20001),
        {**payload(), "extra": 1},
        {"conversation_id": "bad", "message": "hello"},
    ],
)
async def test_invalid_request(client, runtime, body):
    """Malformed values and undeclared fields fail before graph execution."""
    response = await client.post(
        "/v1/agent/stream", json=body, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert runtime.graph.calls == 0


async def test_chunked_body_cap(client, runtime):
    """The total chunked body is bounded before JSON decoding."""

    async def chunks():
        """Send a body without a Content-Length header."""
        yield b" " * (MAX_BODY_BYTES // 2)
        yield b" " * (MAX_BODY_BYTES // 2 + 1)

    response = await client.post(
        "/v1/agent/stream", content=chunks(), headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 413
    assert runtime.graph.calls == 0


async def test_exact_body_cap_reaches_validation(client):
    """A body exactly at the cap reaches JSON validation rather than size rejection."""
    response = await client.post(
        "/v1/agent/stream",
        content=b" " * (MAX_BODY_BYTES),
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 422


async def test_declared_oversize_rejected_without_reading_body(client):
    """An oversized declared length rejects before consuming incoming chunks."""

    async def forbidden_body():
        """Fail if the middleware attempts to consume a known oversized body."""
        raise AssertionError("Body must not be consumed.")
        yield b""  # pragma: no cover

    response = await client.post(
        "/v1/agent/stream",
        content=forbidden_body(),
        headers={
            "Authorization": "Bearer secret",
            "Content-Length": str(MAX_BODY_BYTES + 1),
        },
    )
    assert response.status_code == 413


async def test_huge_declared_length_is_rejected(client):
    """Oversized length text cannot trigger Python integer-conversion failure."""
    response = await client.post(
        "/v1/agent/stream",
        content=b"",
        headers={"Authorization": "Bearer secret", "Content-Length": "9" * 5000},
    )
    assert response.status_code == 413


async def test_successful_event_order(client, runtime):
    """Successful exhaustion emits one completion with increasing sequence IDs."""
    response = await client.post(
        "/v1/agent/stream",
        json=payload("  hello  "),
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    frames = response.text.strip().split("\n\n")
    assert [frame.splitlines()[0] for frame in frames] == [
        "event: status",
        "event: text",
        "event: complete",
    ]
    events = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert len({event["request_id"] for event in events}) == 1
    assert events[-1]["answer"] == "answer"
    assert runtime.admission.active_count == 0
    assert runtime.graph.closed == 1


def test_completion_schema_requires_answer():
    """A successful completion cannot omit its authoritative answer string."""
    with pytest.raises(ValidationError):
        CompleteEvent(request_id=uuid4(), conversation_id=uuid4(), sequence=1)


async def test_complete_uses_final_message_and_survives_missing_client_fragment(
    client, runtime
):
    """Recover the final answer independently of provisional text collected by a client."""
    answer = 'Xin chào! "Kiểm tra máy"\nBước tiếp theo: xác nhận.'
    runtime.graph.fragments = ["Xin ", "chào! ", "provisional fragment"]
    runtime.graph.final_updates = [
        {"answer": {"messages": [AIMessage(content=answer)]}}
    ]
    response = await client.post(
        "/v1/agent/stream", json=payload(), headers={"Authorization": "Bearer secret"}
    )
    events = parse_events(response.text)
    assert [name for name, _ in events] == [
        "status",
        "text",
        "text",
        "text",
        "complete",
    ]
    assert [data["sequence"] for _, data in events] == list(range(1, 6))
    completion = events[-1][1]
    assert completion["answer"] == answer
    assert completion["finish_reason"] == "stop"
    received_fragments = [data["delta"] for name, data in events if name == "text"][::2]
    assert "".join(received_fragments) != answer
    assert completion["answer"] != "".join(
        data["delta"] for name, data in events if name == "text"
    )
    assert runtime.admission.active_count == 0


@pytest.mark.parametrize(
    "updates",
    [
        [],
        [{"answer": {"messages": []}}],
        [{"answer": {"messages": [HumanMessage(content="not an assistant")]}}],
        [{"answer": {"messages": [AIMessage(content=" ")]}}],
        [
            {
                "answer": {
                    "messages": [
                        AIMessage(
                            content=[{"type": "text", "text": "unexpected block"}]
                        )
                    ]
                }
            }
        ],
        [
            {
                "answer": {
                    "messages": [AIMessage(content="one"), AIMessage(content="two")]
                }
            }
        ],
        [{"answer": None}],
        [
            {"answer": {"messages": [AIMessage(content="one")]}},
            {"answer": {"messages": [AIMessage(content="two")]}},
        ],
    ],
)
async def test_invalid_final_output_is_one_persistence_error(client, runtime, updates):
    """Missing, malformed or ambiguous node output cannot fall back to deltas."""
    runtime.graph.final_updates = updates
    response = await client.post(
        "/v1/agent/stream", json=payload(), headers={"Authorization": "Bearer secret"}
    )
    events = parse_events(response.text)
    assert [name for name, _ in events] == ["status", "text", "error"]
    assert events[-1][1]["code"] == "persistence_error"
    assert "answer" not in events[-1][1]
    assert runtime.admission.active_count == 0
    assert runtime.graph.closed == 1


async def test_final_node_result_does_not_acknowledge_failed_save(client, runtime):
    """A node result followed by iterator failure must never emit completion."""
    runtime.graph.fail_after_update = True
    response = await client.post(
        "/v1/agent/stream", json=payload(), headers={"Authorization": "Bearer secret"}
    )
    events = parse_events(response.text)
    assert [name for name, _ in events] == ["status", "text", "error"]
    assert events[-1][1]["code"] == "persistence_error"
    assert "answer" not in events[-1][1]
    assert runtime.admission.active_count == 0
    assert runtime.graph.closed == 1


async def test_complete_waits_for_iterator_persistence_acknowledgement(runtime):
    """A node result alone cannot complete while the final save is still pending."""
    conversation = uuid4()
    runtime.admission.acquire(str(conversation))
    runtime.graph.save_release = asyncio.Event()
    stream = runtime.stream(conversation, "hello", uuid4())
    try:
        assert parse_events(await anext(stream))[0][0] == "status"
        assert parse_events(await anext(stream))[0][0] == "text"
        pending = asyncio.create_task(anext(stream))
        try:
            await asyncio.wait_for(runtime.graph.save_waiting.wait(), 2)
            assert not pending.done()
            runtime.graph.save_release.set()
            event, data = parse_events(await asyncio.wait_for(pending, 2))[0]
            assert event == "complete"
            assert data["answer"] == "answer"
        finally:
            runtime.graph.save_release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    finally:
        await stream.aclose()
    assert runtime.admission.active_count == 0


async def test_admission_conflicts_before_stream(client, runtime):
    """Busy conversations and full capacity fail before opening an event stream."""
    body = payload()
    runtime.admission.acquire(body["conversation_id"])
    response = await client.post(
        "/v1/agent/stream", json=body, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 409
    for _ in range(3):
        runtime.admission.acquire(str(uuid4()))
    response = await client.post(
        "/v1/agent/stream", json=payload(), headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 429
    assert runtime.graph.calls == 0


async def test_timeout_closes_graph_and_releases(client, runtime):
    """An expired request emits one safe error and releases its iterator."""
    runtime.settings.request_timeout = 0.01
    runtime.graph.release = asyncio.Event()
    response = await client.post(
        "/v1/agent/stream", json=payload(), headers={"Authorization": "Bearer secret"}
    )
    assert "event: error" in response.text
    assert '"code":"timeout"' in response.text
    assert "event: complete" not in response.text
    assert runtime.graph.closed == 1
    assert runtime.admission.active_count == 0


async def test_response_start_failure_releases_reservation(runtime):
    """A disconnected transport releases admission before generator startup."""
    from denso_agent.api.stream_response import AgentStreamResponse

    conversation = uuid4()
    runtime.admission.acquire(str(conversation))
    response = AgentStreamResponse(
        runtime.stream(conversation, "hello", uuid4()),
        runtime.admission,
        str(conversation),
    )

    async def disconnected_send(_message):
        """Fail the transport before the iterator receives its first request."""
        raise OSError("client disconnected")

    async def receive():
        """Leave disconnect detection to the failing send call."""
        await asyncio.Event().wait()

    with pytest.raises(Exception):
        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            disconnected_send,
        )
    assert runtime.admission.active_count == 0
    assert runtime.graph.calls == 0


async def test_old_response_cleanup_preserves_new_reservation(runtime):
    """A final body send cannot let an old response clear a newer request."""
    from denso_agent.api.stream_response import AgentStreamResponse

    conversation = uuid4()
    key = str(conversation)
    runtime.admission.acquire(key)
    response = AgentStreamResponse(
        runtime.stream(conversation, "first", uuid4()), runtime.admission, key
    )

    async def send(message):
        """Admit a new request while the old response sends its final empty body."""
        if message["type"] == "http.response.body" and not message.get(
            "more_body", False
        ):
            runtime.admission.acquire(key)

    async def receive():
        """Keep receive unused by the ASGI 2.4 response branch."""
        await asyncio.Event().wait()

    await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert runtime.admission.active_count == 1
    runtime.admission.release(key)


class StateSnapshot:
    """Mimic the values attribute of a LangGraph state snapshot."""

    def __init__(self, messages):
        """Store saved messages under the graph message channel, or none."""
        self.values = {} if messages is None else {"messages": messages}


def saved_state(messages, reads=None):
    """Return a fake aget_state that records configs and returns messages."""

    async def aget_state(config):
        """Record the requested thread and return the controlled snapshot."""
        if reads is not None:
            reads.append(config)
        return StateSnapshot(messages)

    return aget_state


def history_path(conversation_id=None):
    """Build the history route for a new or given conversation."""
    return f"/v1/agent/conversations/{conversation_id or uuid4()}/history"


AUTH = {"Authorization": "Bearer secret"}


async def test_history_requires_service_token_before_reading_state(client, runtime):
    """History reads no checkpoint for a missing or incorrect service token."""
    reads = []
    runtime.graph.aget_state = saved_state([], reads)
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = await client.get(history_path(), headers=headers)
        assert response.status_code == 401
    assert reads == []


async def test_history_rejects_non_uuid_conversation(client, runtime):
    """A malformed conversation ID fails validation without a checkpoint read."""
    reads = []
    runtime.graph.aget_state = saved_state([], reads)
    response = await client.get(
        "/v1/agent/conversations/not-a-uuid/history", headers=AUTH
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert reads == []


async def test_history_of_new_conversation_is_empty(client, runtime):
    """A thread without a checkpoint returns no messages."""
    runtime.graph.aget_state = saved_state(None)
    conversation = uuid4()
    response = await client.get(history_path(conversation), headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": str(conversation),
        "messages": [],
        "truncated": False,
    }


async def test_history_returns_ordered_public_messages_only(client, runtime):
    """Saved user and assistant text keeps order; other message kinds are hidden."""
    from langchain_core.messages import SystemMessage, ToolMessage

    reads = []
    runtime.graph.aget_state = saved_state(
        [
            HumanMessage(content="Máy ép báo lỗi E12"),
            ToolMessage(content="private tool output", tool_call_id="call-1"),
            SystemMessage(content="private instruction"),
            AIMessage(content="Kiểm tra cảm biến áp suất."),
            HumanMessage(content="Đã kiểm tra"),
        ],
        reads,
    )
    conversation = uuid4()
    response = await client.get(history_path(conversation), headers=AUTH)
    assert response.status_code == 200
    assert reads == [{"configurable": {"thread_id": str(conversation)}}]
    assert response.json()["messages"] == [
        {"role": "user", "text": "Máy ép báo lỗi E12", "citations": [], "attachment": None},
        {"role": "assistant", "text": "Kiểm tra cảm biến áp suất.", "citations": [], "attachment": None},
        {"role": "user", "text": "Đã kiểm tra", "citations": [], "attachment": None},
    ]
    assert "private" not in response.text


async def test_history_keeps_newest_messages_within_limit(settings, runtime):
    """A long history returns the newest messages in order and marks truncation."""
    limited = settings.model_copy(update={"history_max_messages": 2})
    runtime.settings = limited
    runtime.graph.aget_state = saved_state(
        [
            HumanMessage(content="one"),
            AIMessage(content="two"),
            HumanMessage(content="three"),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(settings=limited, runtime=runtime)
        ),
        base_url="http://agent.test",
    ) as connection:
        response = await connection.get(history_path(), headers=AUTH)
    body = response.json()
    assert [message["text"] for message in body["messages"]] == ["two", "three"]
    assert body["truncated"] is True


async def test_history_storage_failure_is_unavailable(client, runtime):
    """A checkpoint read failure is reported as 503, not as empty history."""

    async def aget_state(_config):
        """Fail as the database driver would."""
        raise OSError("controlled database failure")

    runtime.graph.aget_state = aget_state
    response = await client.get(history_path(), headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    assert "controlled" not in response.text


CITATION = {
    "marker": "S1",
    "evidence_id": "chunk-71",
    "title": "MR-JE manual",
    "excerpt": "Kiểm tra cảm biến.",
    "location": "trang 71",
}


def cited_answer(text="Kiểm tra [S1]."):
    """Build a saved answer carrying citations and a search status."""
    return AIMessage(
        content=text, additional_kwargs={"citations": [CITATION], "search": "results"}
    )


async def test_search_scope_reaches_the_graph_only_from_the_service_request(
    client, runtime
):
    """The trusted scope is passed in run configuration; absent means no search."""
    body = {**payload(), "search_scope": {"knowledge_bases": ["industrial_qa"]}}
    await client.post("/v1/agent/stream", json=body, headers=AUTH)
    scope = runtime.graph.kwargs["config"]["configurable"]["search_scope"]
    assert scope.knowledge_bases == ["industrial_qa"]

    await client.post("/v1/agent/stream", json=payload(), headers=AUTH)
    assert runtime.graph.kwargs["config"]["configurable"]["search_scope"] is None

    for bad in ({"knowledge_bases": []}, {"knowledge_bases": ["a"], "url": "x"}):
        response = await client.post(
            "/v1/agent/stream", json={**payload(), "search_scope": bad}, headers=AUTH
        )
        assert response.status_code == 422


async def test_complete_and_history_carry_the_saved_citations(client, runtime):
    """Completion and history project citations from the same saved message."""
    runtime.graph.final_updates = [{"answer": {"messages": [cited_answer()]}}]
    response = await client.post("/v1/agent/stream", json=payload(), headers=AUTH)
    complete = [data for name, data in parse_events(response.text) if name == "complete"]
    assert complete[0]["citations"] == [CITATION]
    assert complete[0]["search"] == "results"

    runtime.graph.aget_state = saved_state(
        [HumanMessage(content="E12?"), cited_answer(), AIMessage(content="plain")]
    )
    history = (await client.get(history_path(), headers=AUTH)).json()["messages"]
    assert history == [
        {"role": "user", "text": "E12?", "citations": [], "attachment": None},
        {"role": "assistant", "text": "Kiểm tra [S1].", "citations": [CITATION], "attachment": None},
        {"role": "assistant", "text": "plain", "citations": [], "attachment": None},
    ]


async def test_answer_without_saved_metadata_completes_without_citations(
    client, runtime
):
    """Answers saved before citations existed still complete and reopen."""
    response = await client.post("/v1/agent/stream", json=payload(), headers=AUTH)
    complete = [data for name, data in parse_events(response.text) if name == "complete"]
    assert complete[0]["citations"] == []
    assert complete[0]["search"] == "not_used"


async def test_reasoning_is_streamed_as_its_own_event_and_not_saved(client, runtime):
    """Reasoning parts become `reasoning` events; text and the answer exclude them."""

    original = runtime.graph.astream

    async def astream(*args, **kwargs):
        yield {"type": "custom", "data": {"reasoning": "Kiểm tra mã E12"}}
        async for part in original(*args, **kwargs):
            yield part

    runtime.graph.astream = astream
    response = await client.post("/v1/agent/stream", json=payload(), headers=AUTH)
    events = parse_events(response.text)
    assert [name for name, _ in events] == ["status", "reasoning", "text", "complete"]
    assert events[1][1]["delta"] == "Kiểm tra mã E12"
    assert [data["sequence"] for _, data in events] == [1, 2, 3, 4]
    assert "Kiểm tra mã E12" not in events[-1][1]["answer"]
