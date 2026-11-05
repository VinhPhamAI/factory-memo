"""HTTP contract tests for the knowledge-agent SSE endpoint."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.src.agents.agent.service import (
    STREAM_EVENT_VERSION,
    AgentStreamEvent,
    AgentStreamSession,
    KnowledgeAgentUpstreamError,
)

from backend.src.services.auth import Account, InvalidSession


def _event(sequence: int, event: str, data: dict) -> AgentStreamEvent:
    return AgentStreamEvent(
        version=STREAM_EVENT_VERSION,
        sequence=sequence,
        event=event,
        request_id="request-1",
        thread_id="thread-1",
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        data=data,
    )


class FakeAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def start_stream(self, query: str, *, thread_id: str) -> AgentStreamSession:
        self.calls.append((query, thread_id))

        async def events():
            yield _event(1, "run.started", {"query_chars": len(query)})
            if self.fail:
                raise KnowledgeAgentUpstreamError(
                    "Knowledge agent run failed",
                    request_id="request-1",
                    error_type="FakeFailure",
                )
            yield _event(2, "answer.delta", {"text": "Hello"})
            yield _event(3, "answer.citations", {"citations": []})
            yield _event(4, "run.completed", {"duration_ms": 1})

        return AgentStreamSession(
            request_id="request-1",
            thread_id=thread_id,
            events=events(),
        )


class FakeAuth:
    """Accept the fixture bearer token without opening a database."""

    async def authenticate_access(self, token: str) -> Account:
        """Return an employee account for a valid test token."""

        if token != "qa-test":
            raise InvalidSession()
        return Account(uuid4(), "employee", "Employee", "employee")


def client_for(agents: dict[str, FakeAgent]) -> TestClient:
    """Exercise the authenticated QA routes with controlled agents."""

    return TestClient(
        create_app(knowledge_agents=agents, auth_service=FakeAuth()),
        headers={"Authorization": "Bearer qa-test"},
    )


class KnowledgeAgentApiTest(unittest.TestCase):
    def test_enterprise_endpoint_selects_its_agent_without_body_kb(self) -> None:
        enterprise, general = FakeAgent(), FakeAgent()
        with client_for({
            "enterprise_rag": enterprise, "general": general,
        }) as client:
            response = client.post(
                "/v1/knowledge-agent/enterprise_rag/stream",
                json={"query": "  Who owns ENG-4825?  ", "thread_id": "thread-1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertEqual(enterprise.calls, [("Who owns ENG-4825?", "thread-1")])
        self.assertEqual(general.calls, [])
        self.assertIn("event: answer.citations", response.text)
        self.assertIn("event: run.completed", response.text)

    def test_scoped_endpoint_validates_body_and_unknown_kb(self) -> None:
        agent = FakeAgent()
        with client_for({"enterprise_rag": agent}) as client:
            for body in (
                {"query": " ", "thread_id": "t"},
                {"query": "hi", "thread_id": " "},
                {"query": "hi"},
                {"query": "hi", "thread_id": "t", "kb": "general"},
            ):
                with self.subTest(body=body):
                    response = client.post("/v1/knowledge-agent/enterprise_rag/stream", json=body)
                    self.assertEqual(response.status_code, 422)
            response = client.post(
                "/v1/knowledge-agent/missing/stream",
                json={"query": "hi", "thread_id": "t"},
            )
            self.assertEqual(response.status_code, 404)
        self.assertEqual(agent.calls, [])

    def test_enterprise_stream_reports_agent_failure(self) -> None:
        with client_for({"enterprise_rag": FakeAgent(fail=True)}) as client:
            response = client.post(
                "/v1/knowledge-agent/enterprise_rag/stream",
                json={"query": "question", "thread_id": "t"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: run.failed", response.text)
        self.assertNotIn("event: run.completed", response.text)

    def test_streams_versioned_sse_events(self) -> None:
        agent = FakeAgent()
        with client_for({"general": agent}) as client:
            response = client.post(
                "/v1/knowledge-agent/stream",
                json={"kb": "general", "query": "  sensor?  ", "thread_id": "thread-1"},
                headers={"Accept": "text/event-stream"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(agent.calls, [("sensor?", "thread-1")])
        self.assertIn("event: run.started", response.text)
        self.assertIn('"version":1', response.text)
        self.assertIn('"type":"answer.delta"', response.text)
        self.assertIn("id: request-1:4", response.text)

    def test_converts_stream_errors_to_run_failed(self) -> None:
        with client_for({"general": FakeAgent(fail=True)}) as client:
            response = client.post(
                "/v1/knowledge-agent/stream",
                json={"kb": "general", "query": "sensor?", "thread_id": "thread-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: run.failed", response.text)
        self.assertIn('"sequence":2', response.text)
        self.assertIn('"error_type":"FakeFailure"', response.text)

    def test_rejects_blank_queries_before_starting_stream(self) -> None:
        with client_for({"general": FakeAgent()}) as client:
            response = client.post(
                "/v1/knowledge-agent/stream",
                json={"kb": "general", "query": "   ", "thread_id": "thread-1"},
            )

        self.assertEqual(response.status_code, 422)

    def test_routes_to_the_requested_knowledge_base(self) -> None:
        general, other = FakeAgent(), FakeAgent()
        agents = {"general": general, "other": other}
        with client_for(agents) as client:
            response = client.post(
                "/v1/knowledge-agent/stream",
                json={"kb": "other", "query": "sensor?", "thread_id": "thread-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(general.calls, [])
        self.assertEqual(other.calls, [("sensor?", "thread-1")])

    def test_rejects_unknown_knowledge_base(self) -> None:
        with client_for({"general": FakeAgent()}) as client:
            response = client.post(
                "/v1/knowledge-agent/stream",
                json={"kb": "missing", "query": "sensor?", "thread_id": "thread-1"},
            )

        self.assertEqual(response.status_code, 404)

    def test_requires_a_knowledge_base(self) -> None:
        with client_for({"general": FakeAgent()}) as client:
            response = client.post(
                "/v1/knowledge-agent/stream",
                json={"query": "sensor?", "thread_id": "thread-1"},
            )

        self.assertEqual(response.status_code, 422)

    def test_lists_knowledge_bases(self) -> None:
        agents = {"other": FakeAgent(), "general": FakeAgent()}
        with client_for(agents) as client:
            response = client.get("/v1/knowledge-agent/knowledge-bases")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["name"] for item in response.json()["knowledge_bases"]],
            ["general", "other"],
        )


if __name__ == "__main__":
    unittest.main()
