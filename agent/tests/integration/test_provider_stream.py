"""Exercise actual SDK HTTP fields and incremental graph/API network output."""

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from denso_agent.clients.modelark import ModelArk


@contextmanager
def provider_server(*, reason="stop", fragments=("first", "second")):
    """Serve deterministic OpenAI-shaped SSE with a controllable first-token barrier.

    Args:
        reason: Provider finish reason returned after content.
        fragments: Standard content chunks returned in order.

    Yields:
        Server URL, captured requests, first-token event and continuation barrier.
    """
    captured = []
    first = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        """Capture wire requests and send SDK-compatible stream frames."""

        def log_message(self, *_):
            """Suppress expected fixture access logs."""

        def do_POST(self):  # noqa: N802
            """Send one delta before blocking on the test-controlled barrier."""
            captured.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for index, fragment in enumerate(fragments):
                    if index == 0:
                        # Set before sending: a fast client can otherwise read
                        # the chunk before this thread marks it as sent.
                        first.set()
                    self.send_chunk(fragment, None)
                    if index == 0 and not release.wait(15):
                        return
                self.send_chunk(None, reason)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_chunk(self, content, finish):
            """Write and flush one standard chunk including a private reasoning field."""
            chunk = {
                "id": "stub",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "stub-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": content, "reasoning_content": "THINKING"},
                        "finish_reason": finish,
                    }
                ],
            }
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_port}/api/v3",
            captured,
            first,
            release,
        )
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_actual_sdk_wire_and_incremental_content():
    """Receive a real HTTP first token before allowing provider completion."""
    with provider_server() as (url, requests, first, release):
        settings = SimpleNamespace(
            api_key=SecretStr("test-provider-key"),
            base_url=url,
            request_timeout=10,
            model="stub-model",
            max_output_tokens=71,
        )
        received = asyncio.Event()
        chunks = []

        def writer(part):
            """Record parts and acknowledge the first visible delta."""
            chunks.append(part)
            if "delta" in part:
                received.set()

        async with ModelArk(settings) as model:
            task = asyncio.create_task(
                model.answer([{"role": "user", "content": "hi"}], writer)
            )
            try:
                await asyncio.wait_for(received.wait(), timeout=10)
                assert first.is_set()
                assert not release.is_set()
                assert chunks == [{"reasoning": "THINKING"}, {"delta": "first"}]
                release.set()
                assert await asyncio.wait_for(task, 10) == "firstsecond"
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert len(requests) == 1
        assert requests[0]["path"] == "/api/v3/chat/completions"
        assert requests[0]["authorization"] == "Bearer test-provider-key"
        assert requests[0]["body"] == {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "max_tokens": 71,
        }
        # Reasoning is streamed separately and never becomes answer text.
        assert [part for part in chunks if "delta" in part] == [
            {"delta": "first"},
            {"delta": "second"},
        ]
