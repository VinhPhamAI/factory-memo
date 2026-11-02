"""Verify completed turns survive replacing the installed native CLI process."""

import asyncio
from uuid import uuid4

from denso_agent.config import SetupSettings
from denso_agent.event_loop import new_event_loop
from denso_agent.setup_database import setup_database
from tests.e2e.support import (
    complete_turn,
    native_service,
    provider_server,
    runtime_environment,
)


def test_native_cli_restart_preserves_memory(database, tmp_path):
    """Stop one actual serving process and recall its turn from a replacement."""
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        runner.run(setup_database(SetupSettings(**database)))
    conversation = uuid4()
    with provider_server() as provider:
        env = runtime_environment(database["database_url"], provider.server_port)
        with native_service(env, tmp_path / "first.log") as (url, first):
            complete_turn(url, conversation, "Remember the native code amber")
            first_pid = first.pid
        assert first.poll() is not None
        with asyncio.Runner(loop_factory=new_event_loop) as runner:
            runner.run(setup_database(SetupSettings(**database)))
        with native_service(env, tmp_path / "second.log") as (url, second):
            assert second.pid != first_pid
            complete_turn(url, conversation, "Recall the native code")
            messages = provider.requests[-1]["messages"]
            assert any(
                item["content"] == "Remember the native code amber" for item in messages
            )
            assert any(item["role"] == "assistant" for item in messages)
            complete_turn(url, uuid4(), "Independent conversation")
            assert all(
                item["content"] != "Remember the native code amber"
                for item in provider.requests[-1]["messages"]
            )
