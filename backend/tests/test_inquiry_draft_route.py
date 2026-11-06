"""Checks for POST /v1/conversations/{id}/inquiry-draft and AgentClient.create_inquiry_draft.

PostgreSQL tests need TEST_DATABASE_URL. The agent is a controlled double that
records calls, so tests can prove a denied request never reaches it.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.app import create_app
from backend.src.clients.agent import (
    AgentClient,
    AgentInquiryDraft,
    AgentRejected,
    AgentSettings,
    AgentUnavailable,
)
from backend.src.services.auth import AuthService
from backend.src.services.file_library import FileLibraryService
from backend.src.services.inquiries import InquiryService
from backend.src.storage.app_database import create_session_factory
from backend.src.storage.auth import AuthStore
from backend.src.storage.conversations import ConversationStore
from backend.src.storage.file_library import FileBlobStore, FileRecordStore
from backend.src.storage.inquiries import InquiryStore
from backend.tests.test_auth import FAST_HASHER, SETTINGS
from backend.tests.test_conversations import CITATION, ControlledAgent, ConversationTestCase
from backend.tests.test_database_migrations import DATABASE_URL


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
FIELDS = {
    "title": "Lỗi E12 máy ép số 3",
    "situation": "Máy báo E12.",
    "observations": "Cảm biến nóng.",
    "attempted_actions": "Đã khởi động lại.",
    "proposed_fix": "Thay cảm biến.",
}


def draft_payload(attachments: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"fields": FIELDS, "citations": [CITATION], "attachments": attachments or []}


class DraftAgent(ControlledAgent):
    """ControlledAgent that also answers inquiry-draft requests."""

    def __init__(self) -> None:
        super().__init__()
        self.draft: AgentInquiryDraft | Exception | None = None

    async def create_inquiry_draft(self, conversation_id: UUID) -> AgentInquiryDraft:
        self.calls.append(("draft", conversation_id, None))
        if isinstance(self.draft, Exception):
            raise self.draft
        assert self.draft is not None
        return self.draft


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class InquiryDraftRouteTest(ConversationTestCase):
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

    def upload(self, headers: dict[str, str], name: str = "photo.pdf") -> str:
        response = self.client.post(
            "/v1/files", files={"file": (name, PDF, "application/pdf")}, headers=headers
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def inquiry_count(self) -> int:
        return self.sql("SELECT count(*) FROM inquiries").scalar_one()

    def post(self, headers: dict[str, str], conversation: str) -> Any:
        return self.client.post(f"/v1/conversations/{conversation}/inquiry-draft", headers=headers)

    def test_non_owner_and_deleted_conversation_get_404_without_agent_call(self) -> None:
        owner, other = self.user(), self.user()
        conversation = self.create(owner)["id"]
        self.assertEqual(self.post(other, conversation).status_code, 404)
        self.assertEqual(self.post(owner, str(uuid4())).status_code, 404)
        self.client.delete(f"/v1/conversations/{conversation}", headers=owner)
        self.assertEqual(self.post(owner, conversation).status_code, 404)
        self.assertEqual(self.agent.calls, [])
        self.assertEqual(self.inquiry_count(), 0)

    def test_success_saves_draft_with_citations_and_readable_photos_only(self) -> None:
        owner, other = self.user(), self.user()
        conversation = self.create(owner)["id"]
        mine, foreign = self.upload(owner, "may-ep.pdf"), self.upload(other)
        photos = [
            {"file_id": mine, "media_type": "image/png"},
            {"file_id": foreign, "media_type": "image/png"},
            {"file_id": str(uuid4()), "media_type": "image/png"},
            {"file_id": "not-a-uuid", "media_type": "image/png"},
        ]
        self.agent.draft = AgentInquiryDraft.model_validate(draft_payload(photos))
        response = self.post(owner, conversation)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["title"], FIELDS["title"])
        self.assertEqual(body["conversation_id"], conversation)
        self.assertEqual(body["context"]["situation"], FIELDS["situation"])
        self.assertEqual(body["context"]["proposed_fix"], FIELDS["proposed_fix"])
        self.assertEqual([item["file_id"] for item in body["files"]], [mine])
        evidence = body["context"]["evidence"]
        self.assertEqual([item["kind"] for item in evidence], ["citation", "file"])
        self.assertEqual(evidence[0]["evidence_id"], CITATION["evidence_id"])
        self.assertEqual(evidence[0]["excerpt"], CITATION["excerpt"])
        self.assertEqual((evidence[1]["file_id"], evidence[1]["title"]), (mine, "may-ep.pdf"))
        self.assertEqual(self.agent.calls, [("draft", UUID(conversation), None)])
        self.assertEqual(self.inquiry_count(), 1)

    def test_empty_model_title_is_saved_as_empty_draft_title(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        self.agent.draft = AgentInquiryDraft.model_validate(
            {"fields": {**FIELDS, "title": "  "}, "citations": [], "attachments": []}
        )
        response = self.post(owner, conversation)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["title"], "")
        # The count tests assume an empty table, so remove this test's row.
        self.sql("DELETE FROM inquiries")

    def test_photos_keep_their_evidence_when_citations_exceed_the_limit(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        photo = self.upload(owner)
        citations = [{**CITATION, "evidence_id": f"chunk-{n}"} for n in range(60)]
        citations.insert(0, {**CITATION, "evidence_id": "x" * 201})
        self.agent.draft = AgentInquiryDraft.model_validate(
            {
                "fields": FIELDS,
                "citations": citations,
                "attachments": [{"file_id": photo, "media_type": "image/png"}],
            }
        )
        response = self.post(owner, conversation)
        self.assertEqual(response.status_code, 201, response.text)
        evidence = response.json()["context"]["evidence"]
        self.assertEqual(len(evidence), 50)
        self.assertEqual([item["file_id"] for item in evidence if item["kind"] == "file"], [photo])
        # The over-long ID is skipped, not shortened; citation order is kept.
        cited = [item["evidence_id"] for item in evidence if item["kind"] == "citation"]
        self.assertEqual(cited, [f"chunk-{n}" for n in range(49)])
        # The count tests assume an empty table, so remove this test's rows.
        self.sql("DELETE FROM inquiry_files")
        self.sql("DELETE FROM inquiries")

    def test_over_long_agent_text_is_502_draft_failed_and_saves_nothing(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        self.agent.draft = AgentInquiryDraft.model_validate(
            {"fields": {**FIELDS, "situation": "x" * 4001}, "citations": [], "attachments": []}
        )
        response = self.post(owner, conversation)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "draft_failed")
        self.assertEqual(self.inquiry_count(), 0)

    def test_agent_failures_map_to_statuses_and_save_nothing(self) -> None:
        owner = self.user()
        conversation = self.create(owner)["id"]
        cases = [
            (AgentRejected(422, "empty_conversation", "empty"), 422, "empty_conversation"),
            (AgentRejected(502, "draft_failed", "failed"), 502, "draft_failed"),
            (AgentRejected(409, "conversation_busy", "busy"), 409, "conversation_busy"),
            (AgentRejected(429, "capacity_exceeded", "full"), 429, "capacity_exceeded"),
            (AgentUnavailable("ConnectError"), 503, None),
        ]
        for error, expected, code in cases:
            with self.subTest(status=expected):
                self.agent.draft = error
                response = self.post(owner, conversation)
                self.assertEqual(response.status_code, expected, response.text)
                if code:
                    self.assertEqual(response.json()["detail"]["code"], code)
        self.assertEqual(self.inquiry_count(), 0)

    def test_requires_bearer_credential(self) -> None:
        response = self.client.post(f"/v1/conversations/{uuid4()}/inquiry-draft")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.agent.calls, [])


class AgentClientDraftTest(unittest.TestCase):
    def run_client(self, handler: Any) -> Any:
        async def scenario() -> Any:
            http = httpx.AsyncClient(
                base_url="http://agent", transport=httpx.MockTransport(handler)
            )
            client = AgentClient(AgentSettings("http://agent", "token"), http)
            try:
                return await client.create_inquiry_draft(uuid4())
            finally:
                await client.aclose()

        return asyncio.run(scenario())

    def test_valid_response_and_service_token(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=draft_payload([{"file_id": "f1", "media_type": "image/png"}]))

        draft = self.run_client(handler)
        self.assertEqual(draft.fields.title, FIELDS["title"])
        self.assertEqual(draft.citations[0].evidence_id, "chunk-71")
        self.assertEqual(draft.attachments[0].file_id, "f1")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer token")
        self.assertTrue(seen[0].url.path.endswith("/inquiry-draft"))

    def test_rejections_carry_agent_code(self) -> None:
        for status_code, code in ((422, "empty_conversation"), (502, "draft_failed"), (409, "conversation_busy")):
            with self.subTest(status=status_code):
                handler = lambda request, s=status_code, c=code: httpx.Response(
                    s, json={"error": {"code": c, "message": "m"}}
                )
                with self.assertRaises(AgentRejected) as caught:
                    self.run_client(handler)
                self.assertEqual((caught.exception.status_code, caught.exception.code), (status_code, code))

    def test_unavailable_cases(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        handlers = [
            refuse,
            lambda r: httpx.Response(503, json={"error": {"code": "unavailable", "message": "x"}}),
            lambda r: httpx.Response(401, json={}),
            lambda r: httpx.Response(200, json={"fields": {"title": "only"}}),
            lambda r: httpx.Response(200, content=b"not json"),
        ]
        for handler in handlers:
            with self.subTest(handler=handler):
                with self.assertRaises(AgentUnavailable):
                    self.run_client(handler)


if __name__ == "__main__":
    unittest.main()
