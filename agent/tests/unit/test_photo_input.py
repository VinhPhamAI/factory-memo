"""Photo input: validated request, image only in the current model call."""

import base64

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from denso_agent.app import create_app
from denso_agent.api.body_limit import MAX_BODY_BYTES
from denso_agent.runtime.graph import TurnImage, build_graph
from tests.unit.test_api import (  # noqa: F401  fixtures used by name
    AUTH,
    StateSnapshot,
    client,
    payload,
    runtime,
    settings,
)
from tests.unit.test_retrieval_graph import Provider
from tests.unit.test_retrieval_graph import settings as graph_settings

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
ATTACHMENT = {"file_id": "file-123", "media_type": "image/png", "data": PNG}


async def test_photo_reaches_the_graph_as_context_and_a_saved_reference(
    client, runtime
):
    response = await client.post(
        "/v1/agent/stream", json={**payload(), "attachment": ATTACHMENT}, headers=AUTH
    )
    assert response.status_code == 200
    (graph_input,) = runtime.graph.args
    message = graph_input["messages"][0]
    assert message.additional_kwargs == {
        "attachment": {"file_id": "file-123", "media_type": "image/png"}
    }
    assert PNG not in str(message)
    image = runtime.graph.kwargs["config"]["configurable"]["image"]
    assert isinstance(image, TurnImage)
    assert image.media_type == "image/png"
    assert image.data == base64.b64decode(PNG)


@pytest.mark.parametrize(
    "attachment",
    [
        {**ATTACHMENT, "media_type": "image/gif"},
        {**ATTACHMENT, "media_type": "application/pdf"},
        {**ATTACHMENT, "data": "not base64!"},
        {**ATTACHMENT, "data": ""},
        {**ATTACHMENT, "file_id": ""},
        {**ATTACHMENT, "url": "http://example.com/a.png"},
        {"file_id": "x", "media_type": "image/png"},
    ],
)
async def test_invalid_photos_are_rejected_before_generation(
    client, runtime, attachment
):
    response = await client.post(
        "/v1/agent/stream", json={**payload(), "attachment": attachment}, headers=AUTH
    )
    assert response.status_code == 422
    assert runtime.graph.calls == 0


async def test_oversized_photo_is_rejected(client, runtime):
    big = base64.b64encode(b"\x00" * (3 * 1024 * 1024 + 1)).decode()
    assert len(big) < MAX_BODY_BYTES
    response = await client.post(
        "/v1/agent/stream",
        json={**payload(), "attachment": {**ATTACHMENT, "data": big}},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert runtime.graph.calls == 0


async def test_history_shows_the_photo_reference(client, runtime):
    async def aget_state(_config):
        return StateSnapshot(
            [
                HumanMessage(
                    content="Ảnh này",
                    additional_kwargs={
                        "attachment": {"file_id": "file-123", "media_type": "image/png"}
                    },
                ),
                AIMessage(content="Thấy vết dầu."),
            ]
        )

    runtime.graph.aget_state = aget_state
    response = await client.get(
        "/v1/agent/conversations/11111111-1111-4111-8111-111111111111/history",
        headers=AUTH,
    )
    first, second = response.json()["messages"]
    assert first["attachment"] == {"file_id": "file-123", "media_type": "image/png"}
    assert second["attachment"] is None


async def run(graph, message, image=None, thread="t"):
    config = {"configurable": {"thread_id": thread, "image": image}}
    message = HumanMessage(
        message,
        additional_kwargs=(
            {"attachment": {"file_id": "file-1", "media_type": image.media_type}}
            if image
            else {}
        ),
    )
    async for _ in graph.astream(
        {"messages": [message]},
        config,
        stream_mode="custom",
        version="v2",
        durability="sync",
    ):
        pass
    return (await graph.aget_state(config)).values["messages"]


async def test_only_the_current_turn_sends_image_bytes_to_the_model():
    provider = Provider(answer="Có vết dầu ở khớp nối.")
    graph = build_graph(provider, InMemorySaver(), graph_settings())
    image = TurnImage("image/png", b"\x89PNG-bytes")

    saved = await run(graph, "Đây là ảnh máy", image)

    *_, current = provider.answers[0]
    assert current["role"] == "user"
    assert current["content"][0] == {"type": "text", "text": "Đây là ảnh máy"}
    encoded = base64.b64encode(b"\x89PNG-bytes").decode()
    assert current["content"][1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }
    assert "photo" in provider.answers[0][0]["content"].lower()
    assert saved[0].content == "Đây là ảnh máy"

    await run(graph, "Còn gì nữa?")
    later = provider.answers[1]
    assert all(isinstance(message["content"], str) for message in later)
    assert encoded not in str(later)
