"""Checks for conversation CRUD, ownership and the agent history/stream proxy.

PostgreSQL tests need TEST_DATABASE_URL (see test_database_migrations.py). They
use real signup/login and a controlled agent client that records every call,
so tests can prove that a denied request never reaches the agent.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from collections.abc import AsyncIterator
from typing import Any
from unittest import mock
from uuid import UUID, uuid4

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.app import create_app
from backend.src.clients.agent import AgentHistory, AgentRejected, AgentUnavailable
from backend.src.services.auth import AuthService
from backend.src.storage.app_database import create_session_factory
from backend.src.storage.auth import AuthStore
from backend.src.storage.conversations import ConversationStore
from backend.tests.test_auth import FAST_HASHER, PASSWORD, SETTINGS
from backend.tests.test_database_migrations import (
    DATABASE_URL,
    EMBEDDING_DIM,
    DisposableDatabase,
    alembic_config,
)


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

SSE_FRAMES = [
    b'event: status\ndata: {"sequence":1}\n\n',
    b'event: text\ndata: {"sequence":2,"delta":"Ki\xe1\xbb\x83m"}\n\n',
    b'event: complete\ndata: {"sequence":3,"answer":"Ki\xe1\xbb\x83m tra"}\n\n',
]


class ControlledStream:
    """Upstream SSE response that yields fixed byte chunks and records closing."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def chunks(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class ControlledAgent:
    """Agent client double that records calls and returns configured results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID, Any]] = []
        self.history: AgentHistory | Exception | None = None
        self.stream_error: Exception | None = None
        self.streams: list[ControlledStream] = []

    async def fetch_history(self, conversation_id: UUID) -> AgentHistory:
        self.calls.append(("history", conversation_id, None))
        if isinstance(self.history, Exception):
            raise self.history
        return self.history or AgentHistory(conversation_id=conversation_id, messages=[], truncated=False)

    async def open_stream(self, conversation_id: UUID, message: str) -> ControlledStream:
        self.calls.append(("stream", conversation_id, message))
        if self.stream_error is not None:
            raise self.stream_error
        stream = ControlledStream(SSE_FRAMES)
        self.streams.append(stream)
        return stream


class ConversationTestCase(unittest.TestCase):
    """A migrated disposable database shared by the tests of one class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.database = DisposableDatabase(DATABASE_URL or "")
        cls.database.create()
        try:
            with mock.patch.dict(os.environ, {"EMBEDDING_DIM": str(EMBEDDING_DIM)}):
                command.upgrade(alembic_config(cls.database.url), "head")
            cls.sync_engine = create_engine(cls.database.url)
        except BaseException:
            cls.database.drop()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.sync_engine.dispose()
        cls.database.drop()

    def setUp(self) -> None:
        engine = create_async_engine(self.database.url, poolclass=NullPool)
        sessions = create_session_factory(engine)
        self.agent = ControlledAgent()
        app = create_app(
            knowledge_agents={},
            auth_service=AuthService(AuthStore(sessions), password_hasher=FAST_HASHER),
            auth_settings=SETTINGS,
            conversation_store=ConversationStore(sessions),
            agent_client=self.agent,
        )
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def sql(self, statement: str, **params: Any) -> Any:
        with self.sync_engine.begin() as conn:
            return conn.execute(text(statement), params)

    def user(self) -> dict[str, str]:
        """Sign up and log in a new employee; return its bearer header."""

        username = f"user-{uuid4().hex[:8]}"
        signup = self.client.post(
            "/auth/signup",
            json={"username": username, "display_name": "An", "password": PASSWORD},
        )
        self.assertEqual(signup.status_code, 201, signup.text)
        login = self.client.post("/auth/login", json={"username": username, "password": PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        self.client.cookies.clear()
        return {"Authorization": f"Bearer {login.json()['access_token']}"}

    def create(self, headers: dict[str, str], title: str = "Máy ép số 3") -> dict[str, Any]:
        response = self.client.post("/v1/conversations", json={"title": title}, headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class ConversationCrudTest(ConversationTestCase):
    def test_owner_creates_lists_renames_and_deletes(self) -> None:
        owner = self.user()
        first = self.create(owner, "  Máy ép số 3  ")
        second = self.create(owner, "Băng tải")
        self.assertEqual(first["title"], "Máy ép số 3")
        self.assertEqual(set(first), {"id", "title", "created_at", "updated_at"})

        listed = self.client.get("/v1/conversations", headers=owner).json()["conversations"]
        self.assertEqual([item["id"] for item in listed], [second["id"], first["id"]])

        renamed = self.client.patch(
            f"/v1/conversations/{first['id']}", json={"title": "Máy ép E12"}, headers=owner
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["title"], "Máy ép E12")
        listed = self.client.get("/v1/conversations", headers=owner).json()["conversations"]
        self.assertEqual(listed[0]["id"], first["id"], "renaming counts as recent activity")

        deleted = self.client.delete(f"/v1/conversations/{first['id']}", headers=owner)
        self.assertEqual(deleted.status_code, 204)
        listed = self.client.get("/v1/conversations", headers=owner).json()["conversations"]
        self.assertEqual([item["id"] for item in listed], [second["id"]])
        again = self.client.delete(f"/v1/conversations/{first['id']}", headers=owner)
        self.assertEqual(again.status_code, 404)
        # Soft delete: the row stays for inquiries that reference it.
        row = self.sql("SELECT deleted_at FROM conversations WHERE id = :id", id=first["id"]).one()
        self.assertIsNotNone(row.deleted_at)

    def test_invalid_input_is_rejected(self) -> None:
        owner = self.user()
        for body in ({}, {"title": "   "}, {"title": "x" * 201}, {"title": "a\u0000b"},
                     {"title": "ok", "owner_id": str(uuid4())}):
            with self.subTest(body=body):
                response = self.client.post("/v1/conversations", json=body, headers=owner)
                self.assertEqual(response.status_code, 422)
        response = self.client.get("/v1/conversations/not-a-uuid/history", headers=owner)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.agent.calls, [])

    def test_every_route_requires_a_bearer_credential(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        for method, path, body in (
            ("GET", "/v1/conversations", None),
            ("POST", "/v1/conversations", {"title": "x"}),
            ("PATCH", f"/v1/conversations/{conversation}", {"title": "x"}),
            ("DELETE", f"/v1/conversations/{conversation}", None),
            ("GET", f"/v1/conversations/{conversation}/history", None),
            ("POST", f"/v1/conversations/{conversation}/stream", {"message": "x"}),
        ):
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.agent.calls, [])

    def test_second_user_cannot_see_or_change_a_conversation(self) -> None:
        owner, other = self.user(), self.user()
        conversation = self.create(owner)["id"]

        self.assertEqual(self.client.get("/v1/conversations", headers=other).json(), {"conversations": []})
        for method, path, body in (
            ("PATCH", f"/v1/conversations/{conversation}", {"title": "taken"}),
            ("DELETE", f"/v1/conversations/{conversation}", None),
            ("GET", f"/v1/conversations/{conversation}/history", None),
            ("POST", f"/v1/conversations/{conversation}/stream", {"message": "xin chào"}),
        ):
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, json=body, headers=other)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.agent.calls, [], "a denied request must not reach the agent")
        unchanged = self.client.get("/v1/conversations", headers=owner).json()["conversations"]
        self.assertEqual(unchanged[0]["title"], "Máy ép số 3")

    def test_deleted_or_unknown_conversation_never_reaches_the_agent(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        self.client.delete(f"/v1/conversations/{conversation}", headers=owner)
        for target in (conversation, str(uuid4())):
            for method, path, body in (
                ("GET", f"/v1/conversations/{target}/history", None),
                ("POST", f"/v1/conversations/{target}/stream", {"message": "xin chào"}),
                ("PATCH", f"/v1/conversations/{target}", {"title": "x"}),
            ):
                with self.subTest(target=target, method=method):
                    response = self.client.request(method, path, json=body, headers=owner)
                    self.assertEqual(response.status_code, 404)
        self.assertEqual(self.agent.calls, [])

    def test_deleting_keeps_a_submitted_inquiry_reference(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        owner_id = self.sql("SELECT owner_id FROM conversations WHERE id = :id", id=conversation).scalar_one()
        inquiry = uuid4()
        self.sql(
            "INSERT INTO inquiries (id, conversation_id, requested_by, assigned_to, title, context) "
            "VALUES (:id, :conversation, :owner, :owner, 'E12', '{}'::jsonb)",
            id=inquiry, conversation=conversation, owner=owner_id,
        )

        self.assertEqual(self.client.delete(f"/v1/conversations/{conversation}", headers=owner).status_code, 204)
        kept = self.sql(
            "SELECT i.conversation_id FROM inquiries i JOIN conversations c ON c.id = i.conversation_id "
            "WHERE i.id = :id", id=inquiry,
        ).scalar_one()
        self.assertEqual(str(kept), conversation)

    def test_database_failure_is_503(self) -> None:
        owner = self.user()
        broken = create_async_engine(
            "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
            poolclass=NullPool, connect_args={"connect_timeout": 1},
        )
        self.client.app.state.conversation_store = ConversationStore(create_session_factory(broken))
        response = self.client.get("/v1/conversations", headers=owner)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("nobody", response.text)


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class ConversationAgentProxyTest(ConversationTestCase):
    def test_owner_history_comes_from_the_agent(self) -> None:
        owner = self.user()
        conversation = UUID(self.create(owner)["id"])
        self.agent.history = AgentHistory(
            conversation_id=conversation,
            messages=[{"role": "user", "text": "Lỗi E12"}, {"role": "assistant", "text": "Kiểm tra"}],
            truncated=True,
        )
        response = self.client.get(f"/v1/conversations/{conversation}/history", headers=owner)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "conversation_id": str(conversation),
            "messages": [{"role": "user", "text": "Lỗi E12"}, {"role": "assistant", "text": "Kiểm tra"}],
            "truncated": True,
        })
        self.assertEqual(self.agent.calls, [("history", conversation, None)])

    def test_unavailable_agent_history_is_503(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        self.agent.history = AgentUnavailable("connection refused to agent at 10.0.0.1")
        response = self.client.get(f"/v1/conversations/{conversation}/history", headers=owner)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("10.0.0.1", response.text)

    def test_stream_forwards_agent_frames_and_closes_upstream(self) -> None:
        owner = self.user()
        created = self.create(owner)
        conversation = UUID(created["id"])
        response = self.client.post(
            f"/v1/conversations/{conversation}/stream", json={"message": "  Lỗi E12  "}, headers=owner
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.content, b"".join(SSE_FRAMES))
        self.assertEqual(self.agent.calls, [("stream", conversation, "Lỗi E12")])
        self.assertTrue(self.agent.streams[0].closed)
        listed = self.client.get("/v1/conversations", headers=owner).json()["conversations"]
        self.assertGreater(listed[0]["updated_at"], created["updated_at"], "a message counts as activity")

    def test_stream_rejects_invalid_messages_before_the_agent(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        for body in ({}, {"message": "   "}, {"message": "x" * 20001}, {"message": "x", "extra": 1}):
            with self.subTest(body=body):
                response = self.client.post(f"/v1/conversations/{conversation}/stream", json=body, headers=owner)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.agent.calls, [])

    def test_agent_rejections_before_streaming_keep_their_status(self) -> None:
        owner = self.user()
        created = self.create(owner)
        conversation = created["id"]
        for error, status in (
            (AgentRejected(409, "conversation_busy", "Conversation already has an active request."), 409),
            (AgentRejected(429, "capacity_exceeded", "Agent request capacity is exhausted."), 429),
            (AgentUnavailable("agent down"), 503),
        ):
            with self.subTest(status=status):
                self.agent.stream_error = error
                response = self.client.post(
                    f"/v1/conversations/{conversation}/stream", json={"message": "x"}, headers=owner
                )
                self.assertEqual(response.status_code, status)
                if isinstance(error, AgentRejected):
                    self.assertEqual(response.json()["detail"]["code"], error.code)
        listed = self.client.get("/v1/conversations", headers=owner).json()["conversations"]
        self.assertEqual(listed[0]["updated_at"], created["updated_at"], "a rejected message is not activity")


if __name__ == "__main__":
    unittest.main()
