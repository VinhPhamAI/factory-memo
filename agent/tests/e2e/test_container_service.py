"""Run the actual deployment in a unique disposable Compose project."""

import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx

from tests.e2e.support import (
    REPO,
    HEADERS,
    TOKEN,
    complete_turn,
    free_port,
    native_service,
    provider_server,
    runtime_environment,
    wait_ready,
)
from tests.support.events import parse_events


def test_native_to_container_restart_and_secret_isolation(tmp_path):
    """Switch unchanged env files to Docker, then restart and rerun setup."""
    project = f"denso-agent-test-{uuid4().hex[:12]}"
    pg_port, agent_port = free_port(), free_port()
    runtime_path, setup_path = tmp_path / "runtime.env", tmp_path / "setup.env"
    with provider_server() as provider:
        runtime_url = (
            f"postgresql://agent_runtime:runtime_test@localhost:{pg_port}/agent_memory"
        )
        runtime_path.write_text(
            f"AGENT_DATABASE_URL={runtime_url}\n"
            "AGENT_CONTAINER_DATABASE_URL=postgresql://agent_runtime:runtime_test@postgres:5432/agent_memory\n"
            f"AGENT_API_TOKEN={TOKEN}\nMODELARK_API_KEY=fixture-key\nMODELARK_MODEL=fixture-model\n"
            f"MODELARK_BASE_URL=http://host.docker.internal:{provider.server_port}/v1\n"
            f"POSTGRES_PORT={pg_port}\nAGENT_PORT={agent_port}\n",
            encoding="utf-8",
        )
        setup_path.write_text(
            f"AGENT_BOOTSTRAP_DATABASE_URL=postgresql://voice_agent:voice_agent@localhost:{pg_port}/voice_agent\n"
            f"AGENT_SETUP_DATABASE_URL=postgresql://agent_owner:owner_test@localhost:{pg_port}/agent_memory\n"
            f"AGENT_DATABASE_URL={runtime_url}\n"
            "AGENT_CONTAINER_BOOTSTRAP_DATABASE_URL=postgresql://voice_agent:voice_agent@postgres:5432/voice_agent\n"
            "AGENT_CONTAINER_SETUP_DATABASE_URL=postgresql://agent_owner:owner_test@postgres:5432/agent_memory\n",
            encoding="utf-8",
        )
        unchanged = (runtime_path.read_bytes(), setup_path.read_bytes())
        command = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(runtime_path),
            "--env-file",
            str(setup_path),
            "-f",
            str(REPO / "deployments/docker-compose.yml"),
            "-f",
            str(REPO / "deployments/agent.compose.yml"),
        ]

        def compose(*args, timeout=180):
            """Run only this test's explicitly named Compose project."""
            result = subprocess.run(
                [*command, *args],
                capture_output=True,
                text=True,
                cwd=REPO,
                timeout=timeout,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            return result.stdout

        try:
            compose("up", "-d", "--wait", "postgres")
            setup_env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("AGENT_", "MODELARK_"))
            }
            setup_env.update(
                dict(line.split("=", 1) for line in setup_path.read_text().splitlines())
            )
            import sys  # pylint: disable=import-outside-toplevel

            executable = Path(sys.executable).with_name(
                "denso-agent.exe" if os.name == "nt" else "denso-agent"
            )
            result = subprocess.run(
                [str(executable), "setup-db"],
                env=setup_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, result.stderr
            conversation = uuid4()
            with native_service(
                runtime_environment(runtime_url, provider.server_port),
                tmp_path / "native.log",
            ) as (native_url, _):
                complete_turn(native_url, conversation, "Remember copper")
            compose("up", "--build", "-d", "--wait", "agent", timeout=600)
            url = f"http://127.0.0.1:{agent_port}"
            wait_ready(url)
            complete_turn(url, conversation, "Recall the material")
            assert any(
                message["content"] == "Remember copper"
                for message in provider.requests[-1]["messages"]
            )
            rows = [
                json.loads(line)
                for line in compose("ps", "--all", "--format", "json").splitlines()
            ]
            assert {row["Service"] for row in rows} == {
                "postgres",
                "agent-setup",
                "agent",
            }
            container_id = compose("ps", "-q", "agent").strip()
            inspected = json.loads(
                subprocess.check_output(["docker", "inspect", container_id], text=True)
            )[0]
            environment = inspected["Config"]["Env"]
            assert inspected["Config"]["User"] == "agent"
            assert not any(
                "BOOTSTRAP" in value or "SETUP_DATABASE" in value
                for value in environment
            )
            assert not any(
                "owner_test" in value or "voice_agent:" in value
                for value in environment
            )
            provider.release.clear()
            try:
                with httpx.stream(
                    "POST",
                    f"{url}/v1/agent/stream",
                    headers=HEADERS,
                    json={"conversation_id": str(uuid4()), "message": "Stream"},
                    timeout=30,
                ) as response:
                    lines = response.iter_lines()
                    for line in lines:
                        if line == "event: text":
                            first = json.loads(next(lines).removeprefix("data: "))
                            assert first["delta"] == "fixture "
                            assert not provider.release.is_set()
                            provider.release.set()
                            break
                    else:
                        raise AssertionError(
                            "No first text event arrived before release"
                        )
                    remaining = "\n".join(lines)
                    completion = [
                        data
                        for name, data in parse_events(remaining)
                        if name == "complete"
                    ]
                    assert len(completion) == 1
                    assert completion[0]["answer"] == "fixture answer"
            finally:
                provider.release.set()
            interrupted = str(uuid4())
            provider.release.clear()
            try:
                with httpx.stream(
                    "POST",
                    f"{url}/v1/agent/stream",
                    headers=HEADERS,
                    json={
                        "conversation_id": interrupted,
                        "message": "Interrupt this answer",
                    },
                    timeout=30,
                ) as response:
                    for line in response.iter_lines():
                        if line == "event: text":
                            break
                    else:
                        raise AssertionError("No text before disconnect")
                # Closing the client must free admission; upstream cancellation is best effort.
            finally:
                provider.release.set()
            deadline = time.monotonic() + 15
            while True:
                response = httpx.post(
                    f"{url}/v1/agent/stream",
                    headers=HEADERS,
                    json={
                        "conversation_id": interrupted,
                        "message": "Fresh input after disconnect",
                    },
                    timeout=30,
                )
                if response.status_code != 409:
                    assert response.status_code == 200, response.text
                    assert "event: complete" in response.text, response.text
                    break
                assert (
                    time.monotonic() < deadline
                ), "Disconnected conversation admission leaked"
                time.sleep(0.1)
            failed_conversation = str(uuid4())
            provider.fail_after_first = True
            try:
                response = httpx.post(
                    f"{url}/v1/agent/stream",
                    headers=HEADERS,
                    json={
                        "conversation_id": failed_conversation,
                        "message": "Provider will abort",
                    },
                    timeout=30,
                )
                assert "event: text" in response.text
                assert "event: error" in response.text
                assert '"code":"provider_error"' in response.text
                assert "event: complete" not in response.text
            finally:
                provider.fail_after_first = False
            complete_turn(
                url, failed_conversation, "Fresh input after provider failure"
            )
            assert not any(
                message["role"] == "assistant"
                for message in provider.requests[-1]["messages"]
            )
            compose("restart", "agent")
            wait_ready(url)
            complete_turn(url, conversation, "Recall again")
            assert any(
                message["content"] == "Remember copper"
                for message in provider.requests[-1]["messages"]
            )
            compose("stop", "agent")
            compose("run", "--rm", "agent-setup")
            compose("up", "-d", "--wait", "agent")
            complete_turn(url, conversation, "Recall after setup")
            assert any(
                message["content"] == "Remember copper"
                for message in provider.requests[-1]["messages"]
            )
            assert unchanged == (runtime_path.read_bytes(), setup_path.read_bytes())
        finally:
            # The unique project and its volumes are created by this test only.
            cleanup = subprocess.run(
                [*command, "down", "--volumes", "--remove-orphans"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            assert cleanup.returncode == 0, cleanup.stdout + cleanup.stderr
