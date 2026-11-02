"""Inspect actual PostgreSQL history after completed and interrupted turns."""

import asyncio
from uuid import uuid4

from langchain_core.messages import AIMessage
import psycopg
import pytest

from denso_agent.clients.checkpoints import checkpoint_store, check_readiness
from denso_agent.config import RuntimeSettings, SetupSettings
from denso_agent.runtime.conversation import AgentRuntime
from denso_agent.runtime.errors import AgentError
from denso_agent.runtime.graph import build_graph
from denso_agent.setup_database import setup_database
from tests.support.events import parse_events


class ControlledProvider:
    """Record model context and expose failure/cancellation barriers."""

    def __init__(self, mode="success"):
        """Select a deterministic response mode without a network client."""
        self.mode = mode
        self.inputs = []
        self.started = asyncio.Event()
        self.closed = False

    async def answer(self, messages, writer):
        """Emit provisional text, then complete, fail or wait for cancellation."""
        self.inputs.append(messages)
        self.closed = False
        try:
            writer({"delta": "provisional"})
            self.started.set()
            if self.mode == "failure":
                raise AgentError("provider_error", "Controlled provider failure", True)
            if self.mode == "blocked":
                await asyncio.Event().wait()
            return "complete answer"
        finally:
            self.closed = True


def runtime_settings(database):
    """Build runtime settings with local-only placeholder provider credentials."""
    return RuntimeSettings(
        database_url=database["database_url"],
        api_token="test-token",
        api_key="test-key",
        base_url="http://127.0.0.1:1/v1",
        model="fixture",
    )


async def run_turn(graph, saver, settings, conversation, message):
    """Collect public events after acquiring the ordinary admission gate."""
    runtime = AgentRuntime(graph, lambda: check_readiness(saver), settings)
    runtime.admission.acquire(str(conversation))
    try:
        frames = [
            frame async for frame in runtime.stream(conversation, message, uuid4())
        ]
    finally:
        assert runtime.admission.active_count == 0
    return "".join(frames)


async def test_completed_history_survives_pool_restart_and_is_isolated(database):
    """Rebuild the graph with a new pool and inspect the next model context."""
    await setup_database(SetupSettings(**database))
    settings = runtime_settings(database)
    provider = ControlledProvider()
    conversation = uuid4()
    async with checkpoint_store(database["database_url"]) as saver:
        graph = build_graph(provider, saver, settings)
        frames = await run_turn(graph, saver, settings, conversation, "Remember amber")
        completions = [
            data for name, data in parse_events(frames) if name == "complete"
        ]
        assert len(completions) == 1
        state = await graph.aget_state(
            {"configurable": {"thread_id": str(conversation)}}
        )
        saved_answer = state.values["messages"][-1]
        assert isinstance(saved_answer, AIMessage)
        assert completions[0]["answer"] == saved_answer.content == "complete answer"
        assert completions[0]["answer"] != "provisional"
    await setup_database(SetupSettings(**database))
    async with checkpoint_store(database["database_url"]) as saver:
        graph = build_graph(provider, saver, settings)
        await run_turn(graph, saver, settings, conversation, "Recall the code")
        assert "Remember amber" in str(provider.inputs[-1])
        assert "complete answer" in str(provider.inputs[-1])
        await run_turn(graph, saver, settings, uuid4(), "Independent question")
        assert "Remember amber" not in str(provider.inputs[-1])


@pytest.mark.parametrize("mode", ["failure", "blocked"])
async def test_partial_failure_or_cancellation_then_fresh_input(database, mode):
    """Retain human context without storing provisional text as an assistant turn."""
    await setup_database(SetupSettings(**database))
    settings = runtime_settings(database)
    provider = ControlledProvider(mode)
    conversation = uuid4()
    config = {"configurable": {"thread_id": str(conversation)}}
    async with checkpoint_store(database["database_url"]) as saver:
        graph = build_graph(provider, saver, settings)
        task = asyncio.create_task(
            run_turn(graph, saver, settings, conversation, "First input")
        )
        await asyncio.wait_for(provider.started.wait(), timeout=10)
        if mode == "blocked":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            frames = await task
            assert "event: error" in frames and "event: complete" not in frames
        assert provider.closed
        state = await graph.aget_state(config)
        assert not any(
            isinstance(message, AIMessage)
            for message in state.values.get("messages", [])
        )
        provider.mode = "success"
        frames = await run_turn(graph, saver, settings, conversation, "Fresh input")
        assert "event: complete" in frames
        assert "First input" in str(provider.inputs[-1])
        assert "Fresh input" in str(provider.inputs[-1])
        assert "provisional" not in str(provider.inputs[-1])


async def test_final_checkpoint_failure_never_acknowledges_completion(
    database, monkeypatch
):
    """Fail the final real-saver write and verify a fresh request can still run."""
    await setup_database(SetupSettings(**database))
    settings = runtime_settings(database)
    provider = ControlledProvider()
    conversation = uuid4()
    async with checkpoint_store(database["database_url"]) as saver:
        graph = build_graph(provider, saver, settings)
        original_put = saver.aput
        failed = False

        async def fail_final(config, checkpoint, metadata, new_versions):
            """Inject one database failure when a completed assistant reaches storage."""
            nonlocal failed
            messages = checkpoint.get("channel_values", {}).get("messages", [])
            if (
                any(isinstance(message, AIMessage) for message in messages)
                and not failed
            ):
                failed = True
                raise psycopg.OperationalError("controlled final checkpoint failure")
            return await original_put(config, checkpoint, metadata, new_versions)

        monkeypatch.setattr(saver, "aput", fail_final)
        frames = await run_turn(graph, saver, settings, conversation, "Save this turn")
        assert failed
        assert "event: text" in frames
        assert "event: error" in frames and "event: complete" not in frames
        errors = [data for name, data in parse_events(frames) if name == "error"]
        assert len(errors) == 1
        assert "answer" not in errors[0]
        monkeypatch.setattr(saver, "aput", original_put)
        state = await graph.aget_state(
            {"configurable": {"thread_id": str(conversation)}}
        )
        assert all(
            message.content != "provisional"
            for message in state.values.get("messages", [])
        )
        frames = await run_turn(
            graph, saver, settings, conversation, "Fresh after failed save"
        )
        assert "event: complete" in frames
        assert "Fresh after failed save" in str(provider.inputs[-1])
