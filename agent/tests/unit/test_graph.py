"""Verify context selection and graph completion independently of PostgreSQL."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from denso_agent.runtime.graph import build_graph, select_context
from denso_agent.runtime.errors import AgentError
from types import SimpleNamespace


def settings(**overrides):
    """Return bounded input settings with optional test-specific limits."""
    return SimpleNamespace(
        **{"context_max_messages": 4, "context_max_characters": 100, **overrides}
    )


def test_context_keeps_recent_human_boundary_without_mutation():
    """Select a coherent recent suffix while retaining the stored history."""
    history = [
        HumanMessage("old"),
        AIMessage("old answer"),
        HumanMessage("recent"),
        AIMessage("recent answer"),
        HumanMessage("now"),
    ]
    selected = select_context(history, settings())
    assert [message.content for message in selected] == [
        "recent",
        "recent answer",
        "now",
    ]
    assert len(history) == 5


def test_current_input_must_fit_context():
    """Reject a current message that cannot fit the configured input budget."""
    with pytest.raises(AgentError) as caught:
        select_context([HumanMessage("oversized")], settings(context_max_characters=3))
    assert caught.value.code == "context_limit"


@pytest.mark.asyncio
async def test_graph_custom_v2_stream_and_saved_answer():
    """Prove the installed graph supports custom v2 and synchronous persistence."""

    class Provider:
        """Emit a controlled answer through the node writer."""

        async def answer(self, messages, writer):
            """Check the prompt and stream a single answer."""
            assert messages[0]["role"] == "system"
            writer({"delta": "answer"})
            return "answer"

    graph = build_graph(Provider(), InMemorySaver(), settings())
    config = {"configurable": {"thread_id": "test"}}
    parts = [
        part
        async for part in graph.astream(
            {"messages": [HumanMessage("question")]},
            config,
            stream_mode="custom",
            version="v2",
            durability="sync",
        )
    ]
    assert parts[0]["data"] == {"delta": "answer"}
    saved = await graph.aget_state(config)
    assert [message.content for message in saved.values["messages"]] == [
        "question",
        "answer",
    ]
