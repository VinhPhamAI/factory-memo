"""Execute documented request forms against the real native service."""

import asyncio
import json
import os
import shutil
import subprocess
from uuid import uuid4

from denso_agent.config import SetupSettings
from denso_agent.event_loop import new_event_loop
from denso_agent.setup_database import setup_database
from tests.e2e.support import (
    TOKEN,
    native_service,
    provider_server,
    runtime_environment,
)
from tests.support.events import parse_events


def test_documented_curl_and_powershell_payloads(database, tmp_path):
    """Send the documented file payload with curl and available PowerShell."""
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        runner.run(setup_database(SetupSettings(**database)))
    with provider_server() as provider:
        env = runtime_environment(database["database_url"], provider.server_port)
        with native_service(env, tmp_path / "examples.log") as (url, _):
            payload = tmp_path / "request.json"
            payload.write_text(
                json.dumps(
                    {
                        "conversation_id": str(uuid4()),
                        "message": "Explain the service scope.",
                    }
                ),
                encoding="utf-8",
            )
            curl = shutil.which("curl.exe" if os.name == "nt" else "curl")
            assert curl, "curl is required to verify the documented request"
            result = subprocess.run(
                [
                    curl,
                    "-N",
                    f"{url}/v1/agent/stream",
                    "-H",
                    f"Authorization: Bearer {TOKEN}",
                    "-H",
                    "Content-Type: application/json",
                    "--data-binary",
                    f"@{payload}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert "event: complete" in result.stdout, result.stdout
            completion = [
                data for name, data in parse_events(result.stdout) if name == "complete"
            ]
            assert len(completion) == 1
            assert completion[0]["answer"] == "fixture answer"
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell:
                script = tmp_path / "request.ps1"
                script.write_text(
                    "$ErrorActionPreference = 'Stop'\n"
                    f"$agentPayload = '{payload.read_text()}'\n"
                    f"$response = Invoke-WebRequest -UseBasicParsing -Uri '{url}/v1/agent/stream' "
                    '-Method Post -Headers @{Authorization = "Bearer $env:AGENT_API_TOKEN"} '
                    "-ContentType 'application/json' -Body $agentPayload\n"
                    "$response.Content\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [powershell, "-NoProfile", "-NonInteractive", "-File", str(script)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert result.returncode == 0, result.stderr
                assert "event: complete" in result.stdout, result.stdout
                completion = [
                    data
                    for name, data in parse_events(result.stdout)
                    if name == "complete"
                ]
                assert len(completion) == 1
                assert completion[0]["answer"] == "fixture answer"
