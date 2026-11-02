"""Own temporary provider servers and native service processes."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from tests.support.events import parse_events

AGENT = Path(__file__).resolve().parents[2]
REPO = AGENT.parent
TOKEN = "test-service-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def free_port():
    """Return a loopback port for a test-owned process to bind."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def provider_server():
    """Serve deterministic model chunks and record the actual SDK requests.

    Yields:
        Server with request records and a first-chunk release barrier.
    """

    class Handler(BaseHTTPRequestHandler):
        """Return controlled Chat Completions content over a real socket."""

        def do_POST(self):  # pylint: disable=invalid-name
            """Record a request and stream two standard answer fragments."""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.requests.append(body)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"delta": {"content": "fixture "}, "finish_reason": None},
                {"delta": {"content": "answer"}, "finish_reason": "stop"},
            ]
            try:
                for index, choice in enumerate(chunks):
                    chunk = {
                        "id": "fixture",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": "fixture-model",
                        "choices": [{"index": 0, **choice}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                    if index == 0:
                        self.server.first_sent.set()
                        if not self.server.release.wait(20):
                            return
                        if self.server.fail_after_first:
                            return
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, _format, *args):
            """Keep fixture HTTP logs out of test results."""

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    server.daemon_threads = True
    server.requests = []
    server.release = threading.Event()
    server.release.set()
    server.first_sent = threading.Event()
    server.fail_after_first = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def runtime_environment(database_url, provider_port):
    """Build test runtime settings without inheriting administrative values.

    Args:
        database_url: Restricted native database URL.
        provider_port: Test provider's host TCP port.

    Returns:
        An environment dictionary for native service execution.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "MODELARK_"))
    }
    env.update(
        AGENT_DATABASE_URL=database_url,
        AGENT_API_TOKEN=TOKEN,
        MODELARK_API_KEY="fixture-key",
        MODELARK_MODEL="fixture-model",
        MODELARK_BASE_URL=f"http://127.0.0.1:{provider_port}/v1",
    )
    return env


def wait_ready(url, process=None, timeout=30):
    """Wait for authenticated readiness or fail with a bounded deadline.

    Raises:
        AssertionError: The service exits early or misses its deadline.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise AssertionError(f"Service exited with {process.returncode}")
        try:
            if (
                httpx.get(f"{url}/readyz", headers=HEADERS, timeout=1).status_code
                == 200
            ):
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"Service did not become ready: {url}")


def stop_process(process):
    """Terminate a test-owned process tree, including the reload child."""
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=15,
            )
        else:
            import signal  # pylint: disable=import-outside-toplevel

            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@contextmanager
def native_service(env, log_path, *, executable=None, reload=False):
    """Start the installed CLI and release its process tree after use.

    Args:
        env: Runtime-only settings and ordinary system environment.
        log_path: Temporary file capturing service diagnostics.
        executable: Optional CLI from a temporary editable installation.
        reload: Whether to watch the installed package's source.

    Yields:
        The service URL and subprocess handle.
    """
    port = free_port()
    executable = executable or str(
        Path(sys.executable).with_name(
            "denso-agent.exe" if os.name == "nt" else "denso-agent"
        )
    )
    command = [executable, "serve", "--port", str(port)]
    if reload:
        command.append("--reload")
    options = {"start_new_session": True}
    if os.name == "nt":
        options = {"creationflags": subprocess.CREATE_NO_WINDOW}
    with open(log_path, "w", encoding="utf-8") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log, **options)
        url = f"http://127.0.0.1:{port}"
        try:
            wait_ready(url, process)
            yield url, process
        except Exception as exc:
            raise AssertionError(Path(log_path).read_text(encoding="utf-8")) from exc
        finally:
            stop_process(process)
            with socket.socket() as probe:
                assert (
                    probe.connect_ex(("127.0.0.1", port)) != 0
                ), "Test service port leaked"


def complete_turn(url, conversation_id, message):
    """Submit a real HTTP turn and require the controlled provider's full answer."""
    response = httpx.post(
        f"{url}/v1/agent/stream",
        headers=HEADERS,
        json={"conversation_id": str(conversation_id), "message": message},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    assert "event: complete" in response.text, response.text
    assert "event: error" not in response.text, response.text
    completion = [
        data for name, data in parse_events(response.text) if name == "complete"
    ]
    assert len(completion) == 1, response.text
    assert completion[0]["answer"] == "fixture answer", response.text
    return response.text
