"""Real-HTTP checks of the conversation stream proxy.

The backend and a controlled agent each run in their own uvicorn server, so
the checks cover real sockets: text reaches the browser before the agent
finishes, a browser disconnect closes the agent stream, two conversations
stream at once, and a busy conversation returns 409. No database is used.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from backend.app import create_app
from backend.src.clients.agent import AgentClient, AgentSettings
from backend.src.services.auth import Account, InvalidSession
from backend.src.storage.conversations import ConversationNotFound, ConversationRecord


TOKEN = "service-token"
OWNER = Account(id=uuid4(), username="an", display_name="An", role="employee")


class ControlledAgentServer:
    """Agent stand-in: one active stream per conversation, held until released."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.gates: dict[str, asyncio.Event] = {}
        self.closed: dict[str, threading.Event] = {}
        self.unauthorized = 0
        self.app = Starlette(routes=[Route("/v1/agent/stream", self.stream, methods=["POST"])])

    def release(self, conversation_id: UUID) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self.gates[str(conversation_id)].set)

    async def stream(self, request: Request) -> object:
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            self.unauthorized += 1
            return JSONResponse({"error": {"code": "unauthorized"}}, status_code=401)
        self.loop = asyncio.get_running_loop()
        body = await request.json()
        conversation = body["conversation_id"]
        if conversation in self.gates and not self.closed[conversation].is_set():
            return JSONResponse(
                {"error": {"code": "conversation_busy", "message": "Conversation already has an active request."}},
                status_code=409,
            )
        gate = self.gates[conversation] = asyncio.Event()
        closed = self.closed[conversation] = threading.Event()

        async def frames():
            try:
                yield f"event: text\ndata: {json.dumps({'delta': 'provisional'})}\n\n"
                await gate.wait()
                yield f"event: complete\ndata: {json.dumps({'answer': body['message']}, ensure_ascii=False)}\n\n"
            finally:
                closed.set()

        return StreamingResponse(frames(), media_type="text/event-stream")


class StaticAuth:
    async def authenticate_access(self, token: str) -> Account:
        if token != "owner-access":
            raise InvalidSession()
        return OWNER


class OwnedConversations:
    """Every conversation in `ids` belongs to OWNER."""

    def __init__(self, ids: set[UUID]) -> None:
        self.ids = ids

    async def require_active_owner(self, conversation_id: UUID, owner_id: UUID) -> ConversationRecord:
        if conversation_id not in self.ids or owner_id != OWNER.id:
            raise ConversationNotFound(conversation_id)
        now = datetime.now(timezone.utc)
        return ConversationRecord(conversation_id, "chat", now, now)

    mark_active = require_active_owner


@contextmanager
def serve(app: object) -> Iterator[str]:
    """Run an ASGI app on a free loopback port in a thread; yield its base URL."""

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class ConversationStreamingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = ControlledAgentServer()
        self.first, self.second = uuid4(), uuid4()
        agent_context = serve(self.agent.app)
        agent_url = agent_context.__enter__()
        self.addCleanup(agent_context.__exit__, None, None, None)
        app = create_app(
            knowledge_agents={},
            auth_service=StaticAuth(),
            conversation_store=OwnedConversations({self.first, self.second}),
            agent_client=AgentClient(AgentSettings(base_url=agent_url, api_token=TOKEN)),
        )
        backend_context = serve(app)
        self.backend = backend_context.__enter__()
        self.addCleanup(backend_context.__exit__, None, None, None)
        self.http = httpx.Client(base_url=self.backend, timeout=10)
        self.addCleanup(self.http.close)

    def open(self, conversation: UUID, message: str = "Lỗi E12") -> httpx.Response:
        request = self.http.build_request(
            "POST",
            f"/v1/conversations/{conversation}/stream",
            json={"message": message},
            headers={"Authorization": "Bearer owner-access"},
        )
        return self.http.send(request, stream=True)

    def test_text_arrives_before_the_agent_finishes(self) -> None:
        response = self.open(self.first)
        try:
            self.assertEqual(response.status_code, 200)
            chunks = response.iter_raw()
            first = next(chunks)
            self.assertIn(b"provisional", first)
            self.assertFalse(self.agent.closed[str(self.first)].is_set(), "agent is still generating")
            self.agent.release(self.first)
            rest = b"".join(chunks)
        finally:
            response.close()
        self.assertIn(b"event: complete", rest)
        self.assertIn("Lỗi E12".encode(), rest)
        self.assertTrue(self.agent.closed[str(self.first)].wait(5))
        self.assertEqual(self.agent.unauthorized, 0)

    def test_browser_disconnect_closes_the_agent_stream(self) -> None:
        response = self.open(self.first)
        next(response.iter_raw())
        response.close()
        self.assertTrue(self.agent.closed[str(self.first)].wait(5), "upstream stream must be closed")
        # The conversation is free again for an explicit resend.
        again = self.open(self.first, "gửi lại")
        try:
            self.assertEqual(again.status_code, 200)
        finally:
            again.close()

    def test_two_conversations_stream_at_once_and_a_busy_one_is_409(self) -> None:
        first = self.open(self.first)
        second = self.open(self.second)
        try:
            first_chunks, second_chunks = first.iter_raw(), second.iter_raw()
            self.assertIn(b"provisional", next(first_chunks))
            self.assertIn(b"provisional", next(second_chunks))
            busy = self.open(self.first)
            try:
                self.assertEqual(busy.status_code, 409)
                busy.read()
                self.assertEqual(busy.json()["detail"]["code"], "conversation_busy")
            finally:
                busy.close()
            self.agent.release(self.second)
            self.assertIn(b"event: complete", b"".join(second_chunks))
            self.assertFalse(self.agent.closed[str(self.first)].is_set())
        finally:
            self.agent.release(self.first)
            first.close()
            second.close()

    def test_foreign_conversation_and_bad_token_never_reach_the_agent(self) -> None:
        foreign = self.http.post(
            f"/v1/conversations/{uuid4()}/stream",
            json={"message": "x"},
            headers={"Authorization": "Bearer owner-access"},
        )
        anonymous = self.http.post(
            f"/v1/conversations/{self.first}/stream",
            json={"message": "x"},
            headers={"Authorization": "Bearer other"},
        )
        self.assertEqual((foreign.status_code, anonymous.status_code), (404, 401))
        self.assertEqual(self.agent.gates, {})


if __name__ == "__main__":
    unittest.main()
