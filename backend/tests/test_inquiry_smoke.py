"""End-to-end inquiry smoke test through the HTTP API.

Draft from chat → submit → request clarification → reply → accept. Checks the
timeline, that acceptance creates no knowledge chunks, and that authorized
readers still open the evidence after the file left the library while the
agent is offline. Needs TEST_DATABASE_URL; the agent is a controlled double.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.app import create_app
from backend.src.clients.agent import AgentInquiryDraft, AgentUnavailable
from backend.src.services.auth import AuthService
from backend.src.services.file_library import FileLibraryService
from backend.src.services.inquiries import InquiryService
from backend.src.storage.app_database import create_session_factory
from backend.src.storage.auth import AuthStore
from backend.src.storage.conversations import ConversationStore
from backend.src.storage.file_library import FileBlobStore, FileRecordStore
from backend.src.storage.inquiries import InquiryStore
from backend.tests.test_auth import FAST_HASHER, SETTINGS
from backend.tests.test_conversations import ConversationTestCase
from backend.tests.test_database_migrations import DATABASE_URL
from backend.tests.test_inquiry_draft_route import PDF, DraftAgent, draft_payload


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class InquirySmokeTest(ConversationTestCase):
    def setUp(self) -> None:
        engine = create_async_engine(self.database.url, poolclass=NullPool)
        sessions = create_session_factory(engine)
        self.agent = DraftAgent()
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        library = FileLibraryService(FileRecordStore(sessions), FileBlobStore(root))
        conversations = ConversationStore(sessions)
        app = create_app(
            knowledge_agents={},
            auth_service=AuthService(AuthStore(sessions), password_hasher=FAST_HASHER),
            auth_settings=SETTINGS,
            conversation_store=conversations,
            file_library=library,
            inquiry_service=InquiryService(InquiryStore(sessions), conversations, library),
            agent_client=self.agent,
        )
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def call(self, method: str, path: str, headers: dict[str, str], **kwargs: Any) -> Any:
        response = self.client.request(method, path, headers=headers, **kwargs)
        self.assertLess(response.status_code, 300, f"{method} {path}: {response.text}")
        return response.json() if response.content and "json" in response.headers.get("content-type", "") else None

    def test_draft_submit_question_reply_accept(self) -> None:
        employee, expert, stranger = self.user(), self.user(), self.user()
        expert_id = str(self.client.get("/auth/me", headers=expert).json()["id"])
        self.sql("UPDATE users SET role = 'expert' WHERE id = :id", id=expert_id)
        chunks_before = self.sql("SELECT count(*) FROM knowledge_chunks").scalar_one()

        # A chat with a photo from the employee's library becomes a saved draft.
        photo = self.call(
            "POST", "/v1/files", employee, files={"file": ("e12.pdf", PDF, "application/pdf")}
        )["id"]
        conversation = self.create(employee)["id"]
        self.agent.draft = AgentInquiryDraft.model_validate(
            draft_payload([{"file_id": photo, "media_type": "image/png"}])
        )
        draft = self.call("POST", f"/v1/conversations/{conversation}/inquiry-draft", employee)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual([item["file_id"] for item in draft["files"]], [photo])

        # The employee edits the draft, then submits it to the expert.
        inquiry = draft["id"]
        context = {**draft["context"], "observations": "Cảm biến nóng hơn bình thường."}
        self.call("PATCH", f"/v1/inquiries/{inquiry}", employee, json={"context": context})
        experts = self.call("GET", "/v1/experts", employee)["experts"]
        self.assertIn(expert_id, [item["id"] for item in experts])
        self.call("POST", f"/v1/inquiries/{inquiry}/submit", employee, json={"assigned_to": expert_id})

        # Review: question → reply → accepted.
        events = f"/v1/inquiries/{inquiry}/events"
        self.call("POST", events, expert, json={"kind": "question", "content": "Nhiệt độ bao nhiêu?"})
        self.call("POST", events, employee, json={"kind": "reply", "content": "Khoảng 80 độ C."})
        final = self.call("POST", events, expert, json={"kind": "accepted"})

        self.assertEqual(final["status"], "answered")
        self.assertEqual(
            [(event["kind"], event["actor"]["id"]) for event in final["events"]],
            [
                ("question", expert_id),
                ("reply", final["requested_by"]),
                ("accepted", expert_id),
            ],
        )
        self.assertEqual(self.sql("SELECT count(*) FROM knowledge_chunks").scalar_one(), chunks_before)

        # The file leaves the library and the agent goes offline; readers still open everything.
        self.call("DELETE", f"/v1/files/{photo}", employee)
        self.agent.draft = AgentUnavailable("agent offline")
        calls_before = len(self.agent.calls)
        content = f"/v1/inquiries/{inquiry}/files/{photo}/content"
        for reader in (employee, expert):
            self.assertEqual(self.client.get(f"/v1/inquiries/{inquiry}", headers=reader).status_code, 200)
            response = self.client.get(content, headers=reader)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, PDF)
        self.assertEqual(len(self.agent.calls), calls_before)
        self.assertEqual(self.client.get(f"/v1/inquiries/{inquiry}", headers=stranger).status_code, 404)
        self.assertEqual(self.client.get(content, headers=stranger).status_code, 404)


if __name__ == "__main__":
    unittest.main()
