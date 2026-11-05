"""Verify the discovery-to-answer path with controlled dependencies."""

from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from denso_agent.runtime.graph import TurnContext, build_graph
from denso_agent.runtime.knowledge import Catalog, ToolCall, run_knowledge
from denso_agent.runtime.retrieval import Evidence, RetrievalUnavailable, SearchScope
from denso_agent.schemas.tools import CatalogEntry
from tests.unit.test_retrieval_graph import settings


class Provider:
    """Choose a KB only after receiving the catalog."""

    def __init__(self):
        self.prompts = []
        self.answer_prompt = None

    async def decide_tool(self, messages, _tools):
        """Return the next controlled call."""
        self.prompts.append(messages)
        if len(self.prompts) == 1:
            return ToolCall("call-1", "list_knowledge_bases", {})
        assert "Hydraulics" in str(messages)
        return ToolCall("call-2", "search_knowledge", {"kb": "press", "query": " E42 "})

    async def answer(self, messages, writer):
        """Return a controlled answer with one valid citation."""
        self.answer_prompt = messages
        answer = "See [S1] and [S9]."
        writer({"delta": answer})
        return answer


class Backend:
    """Record calls and return predictable knowledge results."""

    def __init__(self):
        self.calls = []

    async def list(self, credential):
        """Return a catalog containing one available KB."""
        self.calls.append(("list", credential.get_secret_value()))
        return Catalog(
            [CatalogEntry(name="press", title="Press", description="Hydraulics")]
        )

    async def search(self, credential, kb, query):
        """Return one draft result containing a private test excerpt."""
        self.calls.append(("search", credential.get_secret_value(), kb, query))
        return [
            Evidence(
                "chunk-1",
                "Press manual",
                "Pressure switch excerpt",
                "page 2",
                status="draft",
                applicability={"applies_to": "Press P2"},
            )
        ]


async def test_discovery_then_targeted_search_emits_and_saves_safe_tool_data():
    provider, backend = Provider(), Backend()
    saver = InMemorySaver()
    graph = build_graph(provider, saver, settings(), knowledge=backend)
    config = {"configurable": {"thread_id": "conversation"}}
    reply_id = uuid4()
    parts = [
        part["data"]
        async for part in graph.astream(
            {"messages": [HumanMessage("What does E42 mean?")]},
            config,
            context=TurnContext(
                reply_id=reply_id, employee_access=SecretStr("credential-sentinel")
            ),
            stream_mode="custom",
            version="v2",
            durability="sync",
        )
    ]
    assert backend.calls == [
        ("list", "credential-sentinel"),
        ("search", "credential-sentinel", "press", "E42"),
    ]
    tools = [part["tool"] for part in parts if "tool" in part]
    assert [(tool["name"], tool["state"]) for tool in tools] == [
        ("list_knowledge_bases", "running"),
        ("list_knowledge_bases", "results"),
        ("search_knowledge", "running"),
        ("search_knowledge", "results"),
    ]
    assert tools[-1]["results"][0]["excerpt"] == "Pressure switch excerpt"
    assert "Press P2" in str(provider.answer_prompt)
    assert "Hydraulics" not in str(provider.prompts[0])
    assert provider.prompts[0][0] == provider.answer_prompt[0]
    assert (
        "do not give factory-specific settings or procedures"
        in provider.answer_prompt[0]["content"]
    )

    state = await graph.aget_state(config)
    messages = state.values["messages"]
    assert len(messages) == 2
    saved = messages[-1]
    assert saved.id == str(reply_id)
    assert [item["marker"] for item in saved.additional_kwargs["citations"]] == ["S1"]
    assert "Pressure switch excerpt" not in str(saved.additional_kwargs["tools"])
    assert "credential-sentinel" not in repr(list(saver.list(config)))


@pytest.mark.parametrize("fails, expected", [(False, "empty"), (True, "unavailable")])
async def test_live_search_distinguishes_no_matches_from_an_outage(fails, expected):
    """Report empty success and backend failure as different public states."""

    class DirectSearch:
        """Select one KB directly and return no results or an error."""

        async def decide_tool(self, _messages, _tools):
            """Select the user's named KB without listing first."""
            return ToolCall("call", "search_knowledge", {"kb": "press", "query": "E42"})

        async def search(self, _credential, _kb, _query):
            """Simulate an empty backend response or an outage."""
            if fails:
                raise RetrievalUnavailable("fixture failure")
            return []

    fixture = DirectSearch()
    updates = []
    turn = await run_knowledge(
        fixture, [], fixture, SecretStr("test"), None, settings(), updates.append
    )
    assert turn.status == expected
    assert [event["tool"]["state"] for event in updates] == ["running", expected]


async def test_live_search_refuses_a_kb_outside_explicit_scope():
    """An authenticated backend caller can further narrow the allowed KBs."""

    class OutsideScope:
        """Ask for a KB outside the supplied scope."""

        async def decide_tool(self, _messages, _tools):
            """Select an out-of-scope KB."""
            return ToolCall(
                "call", "search_knowledge", {"kb": "private", "query": "E42"}
            )

    backend = Backend()
    turn = await run_knowledge(
        OutsideScope(),
        [],
        backend,
        SecretStr("test"),
        SearchScope(knowledge_bases=["press"]),
        settings(),
        lambda _event: pytest.fail("A rejected call must not be streamed"),
    )
    assert backend.calls == []
    assert turn.status == "unavailable"
