"""Verify source reload from an isolated editable package copy."""

import os
import asyncio
import re
import shutil
import subprocess
import time
from uuid import uuid4

from denso_agent.config import SetupSettings
from denso_agent.setup_database import setup_database
from denso_agent.event_loop import new_event_loop

from tests.e2e.support import (
    AGENT,
    complete_turn,
    native_service,
    provider_server,
    runtime_environment,
)


def test_reload_replaces_server_and_keeps_completed_memory(database, tmp_path):
    """Reload edited temporary source and retain previously completed turns."""
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        runner.run(setup_database(SetupSettings(**database)))
    original_source = (AGENT / "src/denso_agent/app.py").read_bytes()
    copied = tmp_path / "agent"
    shutil.copytree(
        AGENT,
        copied,
        ignore=shutil.ignore_patterns(
            ".venv", ".env", ".env.setup", "__pycache__", ".pytest_cache"
        ),
    )
    result = subprocess.run(
        ["uv", "sync", "--project", str(copied), "--frozen", "--no-dev"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    executable = (
        copied
        / ".venv"
        / ("Scripts/denso-agent.exe" if os.name == "nt" else "bin/denso-agent")
    )
    with provider_server() as provider:
        env = runtime_environment(database["database_url"], provider.server_port)
        log_path = tmp_path / "reload.log"
        conversation = uuid4()
        with native_service(env, log_path, executable=str(executable), reload=True) as (
            url,
            process,
        ):
            complete_turn(url, conversation, "Remember amber")
            original = set(
                re.findall(r"Started server process \[(\d+)\]", log_path.read_text())
            )
            assert original
            source = copied / "src/denso_agent/app.py"
            with source.open("a", encoding="utf-8") as handle:
                handle.write("\n# Temporary reload verification.\n")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                current = set(
                    re.findall(
                        r"Started server process \[(\d+)\]", log_path.read_text()
                    )
                )
                if current - original:
                    break
                assert process.poll() is None
                time.sleep(0.1)
            else:
                raise AssertionError(log_path.read_text())
            from tests.e2e.support import (
                wait_ready,
            )  # pylint: disable=import-outside-toplevel

            wait_ready(url, process)
            complete_turn(url, conversation, "Recall the word")
            assert any(
                message["content"] == "Remember amber"
                for message in provider.requests[-1]["messages"]
            )
    assert (AGENT / "src/denso_agent/app.py").read_bytes() == original_source
    assert original_source != source.read_bytes()
