"""Tests for the scripted incident demo (`DEMO_MODE`).

The route test needs PostgreSQL and TEST_DATABASE_URL; the rest run offline.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.app import create_app
from backend.src.clients.agent import AgentHistory, AgentUnavailable, HistoryMessage
from backend.src.services.demo.proposals import with_demo_proposal
from backend.src.services.demo.scenario import (
    DEMO_MODE_ENV,
    DemoAgentClient,
    DemoScenario,
    read_scenario,
    load_demo_scenario,
    scripted_proposal,
)
from backend.src.services.auth import AuthService
from backend.src.services.demo.accounts import ensure_demo_accounts, load_or_create_accounts
from backend.src.services.demo.scenario import ensure_demo_expert, reset_demo_data
from backend.src.services.file_library import FileLibraryService
from backend.src.services.inquiries import InquiryService
from backend.src.storage.app_database import create_session_factory
from backend.src.storage.auth import AuthStore
from backend.src.storage.conversations import ConversationStore
from backend.src.storage.file_library import FileBlobStore, FileRecordStore
from backend.src.storage.inquiries import DEMO_PROPOSAL_MARKER, InquiryStore
from backend.tests.test_auth import FAST_HASHER, SETTINGS
from backend.tests.test_conversations import ConversationTestCase
from backend.tests.test_database_migrations import DATABASE_URL


def fast_scenario() -> DemoScenario:
    """Return the shipped scenario with every wait set to zero."""

    scenario = read_scenario()
    timing = scenario.timing.model_copy(
        update={"search_seconds": 0, "chunk_delay_ms": 0, "analysis_seconds": 0}
    )
    return scenario.model_copy(update={"timing": timing})


def parse_events(chunks: list[bytes]) -> list[tuple[str, dict]]:
    """Split SSE frames into `(event name, payload)` pairs."""

    events = []
    for chunk in chunks:
        match = re.fullmatch(r"event: (\w+)\ndata: (.*)\n\n", chunk.decode(), re.S)
        assert match is not None, chunk
        events.append((match.group(1), json.loads(match.group(2))))
    return events


class RealAgent:
    """Stands in for the real agent client and records what reached it."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def open_stream(self, conversation_id, message, *, employee_access=None):
        self.calls.append(("stream", conversation_id, message))
        return "real-stream"

    async def fetch_history(self, conversation_id):
        self.calls.append(("history", conversation_id))
        return AgentHistory(
            conversation_id=conversation_id,
            messages=[HistoryMessage(role="user", text="câu hỏi sau")],
            truncated=False,
        )

    async def create_inquiry_draft(self, conversation_id):
        self.calls.append(("draft", conversation_id))
        return "real-draft"


class ScenarioFileTests(unittest.TestCase):
    def test_answer_cites_only_scripted_evidence(self) -> None:
        scenario = read_scenario()
        markers = {item.marker for item in scenario.citations()}
        self.assertEqual(set(re.findall(r"\[(S\d+)\]", scenario.answer)), markers)
        self.assertEqual(
            [item.evidence_id for item in scenario.citations()],
            [str(item.chunk_id) for item in scenario.tool("results").results],
        )

    def test_proposal_fits_the_ticket_field(self) -> None:
        scenario = read_scenario()
        text = with_demo_proposal(scenario.ticket.proposed_fix, "khác", "")
        self.assertLessEqual(
            len(scenario.ticket.proposed_fix) + len(DEMO_PROPOSAL_MARKER) + len(scenario.proposal) + 3,
            4000,
        )
        self.assertIn(DEMO_PROPOSAL_MARKER, text)

    def test_mode_is_off_unless_enabled(self) -> None:
        title = read_scenario().ticket.title
        with mock.patch.dict(os.environ, {DEMO_MODE_ENV: ""}):
            self.assertIsNone(load_demo_scenario())
            self.assertIsNone(scripted_proposal(title))
        with mock.patch.dict(os.environ, {DEMO_MODE_ENV: "true"}):
            scenario = load_demo_scenario()
            self.assertIsNotNone(scenario)
            self.assertEqual(scripted_proposal(title), (scenario.proposal, scenario.timing.analysis_seconds))
            self.assertIsNone(scripted_proposal("Bơm P-101 rò rỉ"))
            block = with_demo_proposal("ý của tôi", title, "")
            self.assertTrue(block.startswith("ý của tôi\n\n" + DEMO_PROPOSAL_MARKER))
            self.assertTrue(block.endswith(scenario.proposal))


class DemoAgentClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_scripted_prompt_streams_agent_events(self) -> None:
        scenario = fast_scenario()
        real = RealAgent()
        client = DemoAgentClient(real, scenario)
        conversation_id = uuid4()

        stream = await client.open_stream(conversation_id, f"  {scenario.prompt.upper()}\n")
        events = parse_events([chunk async for chunk in stream.chunks()])
        await stream.aclose()

        self.assertEqual(real.calls, [])
        names = [name for name, _ in events]
        self.assertEqual(names[0], "status")
        self.assertEqual(names[-1], "complete")
        self.assertEqual(
            list(dict.fromkeys(names)), ["status", "reasoning", "tool", "text", "complete"]
        )
        self.assertEqual([payload["sequence"] for _, payload in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(payload["conversation_id"] == str(conversation_id) for _, payload in events))
        tools = [payload["tool"] for name, payload in events if name == "tool"]
        self.assertEqual([tool["state"] for tool in tools], ["running", "results"])
        self.assertEqual(tools[1]["count"], len(scenario.search.results))
        text = "".join(payload["delta"] for name, payload in events if name == "text")
        self.assertEqual(text, scenario.answer)
        complete = events[-1][1]
        self.assertEqual(complete["answer"], scenario.answer)
        self.assertEqual(complete["search"], "results")
        self.assertEqual([item["marker"] for item in complete["citations"]], ["S1", "S2", "S3"])
        UUID(complete["reply_id"])

    async def test_scripted_conversation_has_history_and_ticket(self) -> None:
        scenario = fast_scenario()
        real = RealAgent()
        client = DemoAgentClient(real, scenario)
        conversation_id = uuid4()
        stream = await client.open_stream(conversation_id, scenario.prompt)
        async for _ in stream.chunks():
            pass

        history = await client.fetch_history(conversation_id)
        self.assertEqual(
            [(item.role, item.text) for item in history.messages],
            [("user", scenario.prompt), ("assistant", scenario.answer), ("user", "câu hỏi sau")],
        )
        self.assertEqual(len(history.messages[1].citations), 3)
        draft = await client.create_inquiry_draft(conversation_id)
        self.assertEqual(draft.fields, scenario.ticket)
        self.assertEqual(len(draft.citations), 3)
        self.assertNotIn(("draft", conversation_id), real.calls)

    async def test_stopped_stream_is_not_remembered(self) -> None:
        scenario = fast_scenario()
        real = RealAgent()
        client = DemoAgentClient(real, scenario)
        conversation_id = uuid4()
        stream = await client.open_stream(conversation_id, scenario.prompt)
        chunks = stream.chunks()
        await anext(chunks)
        await chunks.aclose()

        self.assertEqual(await client.create_inquiry_draft(conversation_id), "real-draft")

    async def test_other_messages_reach_the_real_agent(self) -> None:
        real = RealAgent()
        client = DemoAgentClient(real, fast_scenario())
        conversation_id = uuid4()

        self.assertEqual(await client.open_stream(conversation_id, "Bơm P-101 rò rỉ"), "real-stream")
        self.assertEqual(await client.create_inquiry_draft(conversation_id), "real-draft")
        self.assertEqual(len((await client.fetch_history(conversation_id)).messages), 1)
        self.assertEqual([call[0] for call in real.calls], ["stream", "draft", "history"])

    async def test_without_an_agent_only_the_script_answers(self) -> None:
        client = DemoAgentClient(None, fast_scenario())
        with self.assertRaises(AgentUnavailable):
            await client.open_stream(uuid4(), "câu khác")


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class DemoFlowRouteTest(ConversationTestCase):
    """The four demo steps through the real routes, with no agent behind them."""

    def setUp(self) -> None:
        engine = create_async_engine(self.database.url, poolclass=NullPool)
        self.sessions = create_session_factory(engine)
        self.scenario = fast_scenario()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        library = FileLibraryService(FileRecordStore(self.sessions), FileBlobStore(root))
        conversations = ConversationStore(self.sessions)
        app = create_app(
            knowledge_agents={},
            auth_service=AuthService(AuthStore(self.sessions), password_hasher=FAST_HASHER),
            auth_settings=SETTINGS,
            conversation_store=conversations,
            file_library=library,
            inquiry_service=InquiryService(InquiryStore(self.sessions), conversations, library),
            agent_client=DemoAgentClient(None, self.scenario),
        )
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_chat_ticket_proposal_and_expert_review(self) -> None:
        scenario = self.scenario
        asyncio.run(ensure_demo_expert(self.sessions, scenario.expert))
        asyncio.run(ensure_demo_expert(self.sessions, scenario.expert))
        owner = self.user()
        conversation = self.create(owner)["id"]

        stream = self.client.post(
            f"/v1/conversations/{conversation}/stream",
            json={"message": scenario.prompt},
            headers=owner,
        )
        self.assertEqual(stream.status_code, 200, stream.text)
        self.assertIn(b"event: complete", stream.content)
        history = self.client.get(f"/v1/conversations/{conversation}/history", headers=owner)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(
            [item["text"] for item in history.json()["messages"]], [scenario.prompt, scenario.answer]
        )

        draft = self.client.post(f"/v1/conversations/{conversation}/inquiry-draft", headers=owner)
        self.assertEqual(draft.status_code, 201, draft.text)
        ticket = draft.json()
        self.assertEqual((ticket["title"], ticket["stage"]), (scenario.ticket.title, "todo"))
        self.assertEqual(len(ticket["context"]["evidence"]), 3)

        with mock.patch.dict(os.environ, {DEMO_MODE_ENV: "true"}), mock.patch(
            "backend.api.inquiries.asyncio.sleep", new=mock.AsyncMock()
        ) as wait:
            proposed = self.client.post(f"/v1/inquiries/{ticket['id']}/demo-proposal", headers=owner)
        self.assertEqual(proposed.status_code, 200, proposed.text)
        wait.assert_awaited_once_with(read_scenario().timing.analysis_seconds)
        self.assertEqual(proposed.json()["stage"], "in_progress")
        self.assertTrue(proposed.json()["context"]["proposed_fix"].endswith(scenario.proposal))

        experts = self.client.get("/v1/experts", headers=owner).json()["experts"]
        self.assertEqual([item["display_name"] for item in experts], [scenario.expert.display_name])
        sent = self.client.post(
            f"/v1/inquiries/{ticket['id']}/submit",
            json={"assigned_to": experts[0]["id"]},
            headers=owner,
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertEqual(sent.json()["stage"], "in_review")

        # Reset hides the demo ticket and chat, plus an unticketed demo chat; other data stays.
        asked_only = self.create(owner, " ".join(scenario.prompt.split())[:60])["id"]
        other = self.create(owner, "Robot")["id"]
        self.assertEqual(asyncio.run(reset_demo_data(self.sessions, scenario)), (1, 2))
        self.assertEqual(asyncio.run(reset_demo_data(self.sessions, scenario)), (0, 0))
        self.assertEqual(self.client.get("/v1/inquiries?box=sent", headers=owner).json()["inquiries"], [])
        listed = self.client.get("/v1/conversations", headers=owner).json()["conversations"]
        self.assertEqual([item["id"] for item in listed], [other])
        self.assertNotIn(asked_only, [item["id"] for item in listed])
        self.assertEqual(len(self.client.get("/v1/experts", headers=owner).json()["experts"]), 1)

    def test_demo_accounts_sign_in_with_the_saved_credentials(self) -> None:
        path = Path(tempfile.mkdtemp()) / "demo" / "accounts.json"
        self.addCleanup(shutil.rmtree, path.parent.parent, ignore_errors=True)
        # The mock expert exists first, as after an API start in demo mode.
        asyncio.run(ensure_demo_expert(self.sessions, self.scenario.expert))
        accounts = load_or_create_accounts(path, self.scenario.expert)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_or_create_accounts(path, self.scenario.expert), accounts)

        first = asyncio.run(ensure_demo_accounts(self.sessions, accounts, password_hasher=FAST_HASHER))
        again = asyncio.run(ensure_demo_accounts(self.sessions, accounts, password_hasher=FAST_HASHER))
        self.assertEqual(first, ["created demo.user", "created demo.admin", "updated demo.expert"])
        self.assertEqual([line.split()[0] for line in again], ["updated"] * 3)

        roles = []
        for account in accounts.accounts:
            login = self.client.post(
                "/auth/login", json={"username": account.username, "password": account.password}
            )
            self.assertEqual(login.status_code, 200, login.text)
            self.client.cookies.clear()
            bearer = {"Authorization": f"Bearer {login.json()['access_token']}"}
            roles.append(self.client.get("/auth/me", headers=bearer).json()["role"])
        self.assertEqual(roles, ["employee", "admin", "expert"])
        self.assertEqual(len({account.password for account in accounts.accounts}), 3)


if __name__ == "__main__":
    unittest.main()
