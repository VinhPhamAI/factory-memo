"""Checks for inquiry drafts, experts, submission, access rules and evidence download.

PostgreSQL tests need TEST_DATABASE_URL (see test_database_migrations.py). They
use real signup/login and a temporary file store. No test calls the agent.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.app import create_app
from backend.src.services.auth import AuthService
from backend.src.services.file_library import FileLibraryService
from backend.src.services.inquiries import InquiryContext, InquiryService
from backend.src.storage.app_database import create_session_factory
from backend.src.storage.auth import AuthStore
from backend.src.storage.conversations import ConversationStore
from backend.src.storage.file_library import FileBlobStore, FileRecordStore
from backend.src.storage.inquiries import MAX_ATTACHED_FILES, InquiryStore, InvalidTransition
from backend.tests.test_auth import FAST_HASHER, SETTINGS
from backend.tests.test_conversations import ConversationTestCase
from backend.tests.test_database_migrations import DATABASE_URL


CONTEXT = {
    "situation": "Máy ép số 3 báo lỗi E12",
    "observations": "Đèn đỏ nhấp nháy",
    "attempted_actions": "Khởi động lại",
    "proposed_fix": "Kiểm tra cảm biến",
    "evidence": [],
}
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


class ContextModelTest(unittest.TestCase):
    def test_unknown_keys_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            InquiryContext.model_validate({"situation": "x", "secret": "y"})
        with self.assertRaises(ValueError):
            InquiryContext.model_validate({"evidence": [{"kind": "file", "title": "t", "extra": 1}]})

    def test_defaults_are_empty(self) -> None:
        self.assertEqual(InquiryContext().model_dump()["evidence"], [])


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class InquiryApiTest(ConversationTestCase):
    def setUp(self) -> None:
        engine = create_async_engine(self.database.url, poolclass=NullPool)
        sessions = create_session_factory(engine)
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        library = FileLibraryService(FileRecordStore(sessions), FileBlobStore(self.root))
        conversations = ConversationStore(sessions)
        app = create_app(
            knowledge_agents={},
            auth_service=AuthService(AuthStore(sessions), password_hasher=FAST_HASHER),
            auth_settings=SETTINGS,
            conversation_store=conversations,
            file_library=library,
            inquiry_service=InquiryService(InquiryStore(sessions), conversations, library),
        )
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def expert(self, **changes: Any) -> tuple[dict[str, str], str]:
        """Sign up a user, promote it to expert; return its header and ID."""

        headers = self.user()
        user_id = str(self.client.get("/auth/me", headers=headers).json()["id"])
        self.sql("UPDATE users SET role = 'expert' WHERE id = :id", id=user_id)
        for column, value in changes.items():
            self.sql(f"UPDATE users SET {column} = {value} WHERE id = :id", id=user_id)
        return headers, user_id

    def draft(self, headers: dict[str, str], **body: Any) -> dict[str, Any]:
        conversation = self.create(headers)["id"]
        payload = {"conversation_id": conversation, "title": "Lỗi E12", "context": CONTEXT, **body}
        response = self.client.post("/v1/inquiries", json=payload, headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self, headers: dict[str, str], name: str = "photo.pdf") -> str:
        response = self.client.post(
            "/v1/files", files={"file": (name, PDF, "application/pdf")}, headers=headers
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def submit(self, headers: dict[str, str], inquiry: str, expert_id: str) -> Any:
        return self.client.post(
            f"/v1/inquiries/{inquiry}/submit", json={"assigned_to": expert_id}, headers=headers
        )

    def test_owner_creates_lists_reads_edits_and_deletes_a_draft(self) -> None:
        owner = self.user()
        draft = self.draft(owner)
        self.assertEqual(draft["status"], "draft")
        self.assertIsNone(draft["assigned_to"])
        self.assertIsNone(draft["submitted_at"])
        self.assertEqual(draft["context"]["situation"], CONTEXT["situation"])
        self.assertEqual((draft["files"], draft["events"]), ([], []))

        listed = self.client.get("/v1/inquiries?box=sent", headers=owner).json()["inquiries"]
        self.assertEqual([item["id"] for item in listed], [draft["id"]])
        self.assertNotIn("context", listed[0])
        self.assertEqual(self.client.get(f"/v1/inquiries/{draft['id']}", headers=owner).status_code, 200)

        patched = self.client.patch(
            f"/v1/inquiries/{draft['id']}",
            json={"title": "  Lỗi E13 ", "context": {**CONTEXT, "situation": "Mới"}},
            headers=owner,
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["title"], "Lỗi E13")
        self.assertEqual(patched.json()["context"]["situation"], "Mới")

        self.assertEqual(self.client.delete(f"/v1/inquiries/{draft['id']}", headers=owner).status_code, 204)
        self.assertEqual(self.client.get(f"/v1/inquiries/{draft['id']}", headers=owner).status_code, 404)
        self.assertEqual(self.client.get("/v1/inquiries", headers=owner).json(), {"inquiries": []})
        row = self.sql("SELECT deleted_at FROM inquiries WHERE id = :id", id=draft["id"]).one()
        self.assertIsNotNone(row.deleted_at)

    def test_non_owner_gets_404_for_every_draft_route(self) -> None:
        owner, other = self.user(), self.user()
        assignee, assignee_id = self.expert()
        draft = self.draft(owner)["id"]
        file_id = self.upload(other)
        for who in (other, assignee):
            for method, path, body in (
                ("GET", f"/v1/inquiries/{draft}", None),
                ("PATCH", f"/v1/inquiries/{draft}", {"title": "x"}),
                ("DELETE", f"/v1/inquiries/{draft}", None),
                ("PUT", f"/v1/inquiries/{draft}/files/{file_id}", None),
                ("DELETE", f"/v1/inquiries/{draft}/files/{file_id}", None),
                ("POST", f"/v1/inquiries/{draft}/submit", {"assigned_to": assignee_id}),
                ("GET", f"/v1/inquiries/{draft}/files/{file_id}/content", None),
            ):
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path, json=body, headers=who)
                    self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.client.get("/v1/inquiries", headers=other).json(), {"inquiries": []})
        self.assertEqual(
            self.client.get(f"/v1/inquiries/{uuid4()}", headers=owner).status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/v1/inquiries/{draft}", headers=owner).json()["title"], "Lỗi E12"
        )

    def test_draft_is_hidden_from_the_assignee_box_until_submitted(self) -> None:
        owner = self.user()
        assignee, assignee_id = self.expert()
        draft = self.draft(owner)["id"]
        self.assertEqual(self.client.get("/v1/inquiries?box=assigned", headers=assignee).json(), {"inquiries": []})

        submitted = self.submit(owner, draft, assignee_id)
        self.assertEqual(submitted.status_code, 200, submitted.text)
        body = submitted.json()
        self.assertEqual((body["status"], body["assigned_to"]), ("open", assignee_id))
        self.assertIsNotNone(body["submitted_at"])
        assigned = self.client.get("/v1/inquiries?box=assigned", headers=assignee).json()["inquiries"]
        self.assertEqual([item["id"] for item in assigned], [draft])
        self.assertEqual(self.client.get(f"/v1/inquiries/{draft}", headers=assignee).status_code, 200)
        # The requester still sees it in their sent box.
        sent = self.client.get("/v1/inquiries", headers=owner).json()["inquiries"]
        self.assertEqual([item["id"] for item in sent], [draft])

    def test_experts_list_excludes_other_roles_and_disabled_accounts(self) -> None:
        owner = self.user()
        _, expert_id = self.expert()
        _, inactive_id = self.expert(is_active="false")
        _, deleted_id = self.expert(deleted_at="now()")
        admin = self.user()
        admin_id = str(self.client.get("/auth/me", headers=admin).json()["id"])
        self.sql("UPDATE users SET role = 'admin' WHERE id = :id", id=admin_id)
        employee_id = str(self.client.get("/auth/me", headers=owner).json()["id"])

        listed = self.client.get("/v1/experts", headers=owner).json()["experts"]
        ids = {item["id"] for item in listed}
        self.assertIn(expert_id, ids)
        self.assertTrue({inactive_id, deleted_id, admin_id, employee_id}.isdisjoint(ids))
        self.assertEqual(set(listed[0]), {"id", "display_name"})

        draft = self.draft(owner)["id"]
        for target in (inactive_id, deleted_id, admin_id, employee_id, str(uuid4())):
            with self.subTest(target=target):
                response = self.submit(owner, draft, target)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], "ineligible_assignee")
        self.assertEqual(self.client.get(f"/v1/inquiries/{draft}", headers=owner).json()["status"], "draft")
        self.assertEqual(self.submit(owner, draft, expert_id).status_code, 200)

    def test_role_change_after_listing_is_rechecked_at_submit(self) -> None:
        owner = self.user()
        _, expert_id = self.expert()
        draft = self.draft(owner)["id"]
        self.sql("UPDATE users SET role = 'employee' WHERE id = :id", id=expert_id)
        self.assertEqual(self.submit(owner, draft, expert_id).status_code, 422)

    def test_expert_cannot_assign_their_own_inquiry_to_themselves(self) -> None:
        expert, expert_id = self.expert()
        draft = self.draft(expert)["id"]
        response = self.submit(expert, draft, expert_id)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "ineligible_assignee")

    def test_submit_requires_title_and_situation(self) -> None:
        owner = self.user()
        _, expert_id = self.expert()
        for body in (
            {"title": "", "context": CONTEXT},
            {"title": "Lỗi", "context": {**CONTEXT, "situation": "  "}},
            {"title": "Lỗi", "context": {}},
        ):
            with self.subTest(body=body):
                draft = self.draft(owner, **body)["id"]
                response = self.submit(owner, draft, expert_id)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], "invalid_context")
        partial = self.draft(owner, context={"situation": "chỉ có tình huống"})
        self.assertEqual(self.submit(owner, partial["id"], expert_id).status_code, 200)

    def test_submitted_inquiry_rejects_edits_with_409(self) -> None:
        owner = self.user()
        _, expert_id = self.expert()
        draft = self.draft(owner)["id"]
        file_id = self.upload(owner)
        self.assertEqual(self.submit(owner, draft, expert_id).status_code, 200)
        for method, path, body in (
            ("PATCH", f"/v1/inquiries/{draft}", {"title": "x"}),
            ("DELETE", f"/v1/inquiries/{draft}", None),
            ("PUT", f"/v1/inquiries/{draft}/files/{file_id}", None),
            ("DELETE", f"/v1/inquiries/{draft}/files/{file_id}", None),
            ("POST", f"/v1/inquiries/{draft}/submit", {"assigned_to": expert_id}),
        ):
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, json=body, headers=owner)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "invalid_transition")
        self.assertEqual(self.client.get(f"/v1/inquiries/{draft}", headers=owner).json()["title"], "Lỗi E12")

    def test_attach_detach_and_readability(self) -> None:
        owner, other = self.user(), self.user()
        draft = self.draft(owner)["id"]
        mine, foreign = self.upload(owner), self.upload(other)

        attached = self.client.put(f"/v1/inquiries/{draft}/files/{mine}", headers=owner)
        self.assertEqual(attached.status_code, 204)
        self.assertEqual(self.client.put(f"/v1/inquiries/{draft}/files/{mine}", headers=owner).status_code, 204)
        files = self.client.get(f"/v1/inquiries/{draft}", headers=owner).json()["files"]
        self.assertEqual([item["file_id"] for item in files], [mine])
        self.assertEqual(set(files[0]), {"file_id", "filename", "content_type", "size_bytes", "event_id"})

        for bad in (foreign, str(uuid4())):
            response = self.client.put(f"/v1/inquiries/{draft}/files/{bad}", headers=owner)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["detail"]["code"], "unreadable_file")
        deleted = self.upload(owner)
        self.client.delete(f"/v1/files/{deleted}", headers=owner)
        self.assertEqual(self.client.put(f"/v1/inquiries/{draft}/files/{deleted}", headers=owner).status_code, 422)

        self.assertEqual(self.client.delete(f"/v1/inquiries/{draft}/files/{mine}", headers=owner).status_code, 204)
        self.assertEqual(self.client.get(f"/v1/inquiries/{draft}", headers=owner).json()["files"], [])

    def test_attaching_beyond_the_file_limit_is_rejected(self) -> None:
        owner = self.user()
        draft = self.draft(owner)["id"]
        for _ in range(MAX_ATTACHED_FILES):
            file_id = self.upload(owner)
            self.assertEqual(self.client.put(f"/v1/inquiries/{draft}/files/{file_id}", headers=owner).status_code, 204)
        # Re-attaching an attached file stays a no-op; a new one is refused.
        self.assertEqual(self.client.put(f"/v1/inquiries/{draft}/files/{file_id}", headers=owner).status_code, 204)
        response = self.client.put(f"/v1/inquiries/{draft}/files/{self.upload(owner)}", headers=owner)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "invalid_context")
        files = self.client.get(f"/v1/inquiries/{draft}", headers=owner).json()["files"]
        self.assertEqual(len(files), MAX_ATTACHED_FILES)

    def test_detach_removes_evidence_that_refers_to_the_file(self) -> None:
        owner = self.user()
        mine, kept = self.upload(owner), self.upload(owner)
        evidence = [
            {"kind": "file", "title": "A", "file_id": mine},
            {"kind": "file", "title": "B", "file_id": kept},
            {"kind": "citation", "title": "C", "evidence_id": "chunk-1"},
        ]
        draft = self.draft(owner, file_ids=[mine, kept], context={**CONTEXT, "evidence": evidence})["id"]
        self.assertEqual(self.client.delete(f"/v1/inquiries/{draft}/files/{mine}", headers=owner).status_code, 204)
        body = self.client.get(f"/v1/inquiries/{draft}", headers=owner).json()
        self.assertEqual([item["title"] for item in body["context"]["evidence"]], ["B", "C"])
        self.assertEqual([item["file_id"] for item in body["files"]], [kept])

    def test_validation_runs_on_the_row_read_under_the_lock(self) -> None:
        """Store callbacks receive the stored title, context and attached files."""

        owner = self.user()
        _, expert_id = self.expert()
        file_id = self.upload(owner)
        draft = self.draft(owner, file_ids=[file_id])["id"]
        seen: list[Any] = []
        sessions = create_session_factory(create_async_engine(self.database.url, poolclass=NullPool))

        def validator(title: str, context: dict, attached: set[UUID]) -> None:
            seen.append((title, context["situation"], attached))

        asyncio.run(InquiryStore(sessions).submit(UUID(draft), UUID(self.client.get("/auth/me", headers=owner).json()["id"]), UUID(expert_id), validator))
        self.assertEqual(seen, [("Lỗi E12", CONTEXT["situation"], {UUID(file_id)})])

    def test_create_with_files_and_evidence_validation(self) -> None:
        owner, other = self.user(), self.user()
        mine, foreign = self.upload(owner), self.upload(other)
        evidence = {"kind": "file", "title": "Ảnh", "file_id": mine}
        created = self.draft(owner, file_ids=[mine, mine], context={**CONTEXT, "evidence": [evidence]})
        self.assertEqual([item["file_id"] for item in created["files"]], [mine])

        conversation = self.create(owner)["id"]
        for body in (
            {"file_ids": [foreign]},
            {"context": {**CONTEXT, "evidence": [evidence]}},  # evidence file not attached
            {"context": {**CONTEXT, "unknown": 1}},
            {"title": "x" * 201},
            {"unknown": 1},
        ):
            with self.subTest(body=body):
                response = self.client.post(
                    "/v1/inquiries", json={"conversation_id": conversation, **body}, headers=owner
                )
                self.assertEqual(response.status_code, 422, response.text)

    def test_create_requires_an_active_owned_conversation(self) -> None:
        owner, other = self.user(), self.user()
        conversation = self.create(owner)["id"]
        deleted = self.create(owner)["id"]
        self.client.delete(f"/v1/conversations/{deleted}", headers=owner)
        for target, who in ((conversation, other), (deleted, owner), (str(uuid4()), owner)):
            with self.subTest(target=target):
                response = self.client.post(
                    "/v1/inquiries", json={"conversation_id": target, "title": "x"}, headers=who
                )
                self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.client.get("/v1/inquiries", headers=owner).json(), {"inquiries": []})

    def test_evidence_download_survives_library_deletion(self) -> None:
        owner, stranger = self.user(), self.user()
        assignee, assignee_id = self.expert()
        file_id = self.upload(owner)
        draft = self.draft(owner, file_ids=[file_id])["id"]
        url = f"/v1/inquiries/{draft}/files/{file_id}/content"

        mine = self.client.get(url, headers=owner)
        self.assertEqual((mine.status_code, mine.content), (200, PDF))
        self.assertIn("attachment", mine.headers["content-disposition"])
        self.assertEqual(mine.headers["x-content-type-options"], "nosniff")
        self.assertEqual(mine.headers["cache-control"], "private, no-store")
        self.assertEqual(self.client.get(url, headers=assignee).status_code, 404, "draft is private")

        self.submit(owner, draft, assignee_id)
        self.assertEqual(self.client.delete(f"/v1/files/{file_id}", headers=owner).status_code, 204)
        self.assertEqual(self.client.get(f"/v1/files/{file_id}/content", headers=owner).status_code, 404)
        for who in (owner, assignee):
            response = self.client.get(url, headers=who)
            self.assertEqual((response.status_code, response.content), (200, PDF))
        self.assertEqual(self.client.get(url, headers=stranger).status_code, 404)
        self.assertEqual(
            self.client.get(f"/v1/inquiries/{draft}/files/{uuid4()}/content", headers=owner).status_code, 404
        )
        self.assertNotIn("storage_key", self.client.get(f"/v1/inquiries/{draft}", headers=owner).text)

    def test_deleting_the_conversation_keeps_the_inquiry_readable(self) -> None:
        owner = self.user()
        draft = self.draft(owner)
        self.client.delete(f"/v1/conversations/{draft['conversation_id']}", headers=owner)
        response = self.client.get(f"/v1/inquiries/{draft['id']}", headers=owner)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Lỗi E12")

    def test_routes_require_a_bearer_credential(self) -> None:
        for method, path in (
            ("GET", "/v1/inquiries"), ("POST", "/v1/inquiries"), ("GET", f"/v1/inquiries/{uuid4()}"),
            ("GET", "/v1/experts"),
            ("GET", f"/v1/inquiries/{uuid4()}/files/{uuid4()}/content"),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method, path).status_code, 401)

    def test_invalid_box_is_rejected(self) -> None:
        self.assertEqual(self.client.get("/v1/inquiries?box=all", headers=self.user()).status_code, 422)

    def open_inquiry(self) -> tuple[dict[str, str], str, dict[str, str], str, str]:
        """Return requester header and ID, assignee header and ID, and an `open` inquiry ID."""

        owner = self.user()
        owner_id = str(self.client.get("/auth/me", headers=owner).json()["id"])
        assignee, assignee_id = self.expert()
        inquiry = self.draft(owner)["id"]
        self.assertEqual(self.submit(owner, inquiry, assignee_id).status_code, 200)
        return owner, owner_id, assignee, assignee_id, inquiry

    def act(self, headers: dict[str, str], inquiry: str, kind: str, content: Any = None) -> Any:
        body: dict[str, Any] = {"kind": kind}
        if content is not None:
            body["content"] = content
        return self.client.post(f"/v1/inquiries/{inquiry}/events", json=body, headers=headers)

    def event_count(self, inquiry: str) -> int:
        return self.sql("SELECT count(*) FROM inquiry_events WHERE inquiry_id = :i", i=inquiry).scalar_one()

    def test_assignee_actions_move_open_to_their_next_status(self) -> None:
        for kind, content, final in (
            ("accepted", None, "answered"), ("edited", " Dùng cảm biến B ", "answered"),
            ("denied", "Không áp dụng", "closed"), ("question", "Mã máy nào?", "needs_info"),
        ):
            with self.subTest(kind=kind):
                owner, _, assignee, assignee_id, inquiry = self.open_inquiry()
                response = self.act(assignee, inquiry, kind, content)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["status"], final)
                (event,) = body["events"]
                self.assertEqual(set(event), {"id", "kind", "content", "actor", "created_at"})
                self.assertEqual(event["kind"], kind)
                self.assertEqual(event["content"], content.strip() if content else None)
                self.assertEqual(event["actor"], {"id": assignee_id, "display_name": "An"})
                self.assertEqual(self.client.get(f"/v1/inquiries/{inquiry}", headers=owner).json()["status"], final)

    def test_requester_reply_reopens_a_needs_info_inquiry(self) -> None:
        owner, owner_id, assignee, _, inquiry = self.open_inquiry()
        self.assertEqual(self.act(assignee, inquiry, "question", "Mã máy nào?").status_code, 200)
        response = self.act(owner, inquiry, "reply", "Máy số 3")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "open")
        self.assertEqual(response.json()["events"][-1]["actor"]["id"], owner_id)

    def test_disallowed_actions_return_409_and_write_nothing(self) -> None:
        owner, _, assignee, _, inquiry = self.open_inquiry()
        cases = [
            (owner, "accepted", None), (owner, "edited", "x"), (owner, "denied", "x"),
            (owner, "question", "x"), (owner, "reply", "x"), (assignee, "reply", "x"),
        ]
        for who, kind, content in cases:
            with self.subTest(status="open", kind=kind, requester=who is owner):
                response = self.act(who, inquiry, kind, content)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "invalid_transition")
        self.assertEqual(self.event_count(inquiry), 0)

        self.assertEqual(self.act(assignee, inquiry, "question", "Mã máy nào?").status_code, 200)
        for who, kind, content in (
            (assignee, "accepted", None), (assignee, "denied", "x"), (assignee, "reply", "x"),
            (assignee, "question", "x"), (owner, "accepted", None), (owner, "question", "x"),
        ):
            with self.subTest(status="needs_info", kind=kind, requester=who is owner):
                self.assertEqual(self.act(who, inquiry, kind, content).status_code, 409)
        self.assertEqual(self.event_count(inquiry), 1)

    def test_answered_and_closed_inquiries_accept_no_actions(self) -> None:
        for kind, content in (("accepted", None), ("denied", "Không áp dụng")):
            owner, _, assignee, _, inquiry = self.open_inquiry()
            self.assertEqual(self.act(assignee, inquiry, kind, content).status_code, 200)
            for who in (owner, assignee):
                for other in ("accepted", "edited", "denied", "question", "reply"):
                    with self.subTest(first=kind, next=other):
                        self.assertEqual(self.act(who, inquiry, other, "x").status_code, 409)
            self.assertEqual(self.event_count(inquiry), 1)

    def test_events_on_a_draft_and_by_unrelated_users(self) -> None:
        owner = self.user()
        assignee, assignee_id = self.expert()
        stranger = self.user()
        draft = self.draft(owner)["id"]
        self.assertEqual(self.act(owner, draft, "reply", "x").status_code, 409)
        self.assertEqual(self.act(assignee, draft, "accepted").status_code, 404)
        self.assertEqual(self.act(stranger, draft, "accepted").status_code, 404)
        self.assertEqual(self.submit(owner, draft, assignee_id).status_code, 200)
        self.assertEqual(self.act(stranger, draft, "accepted").status_code, 404)
        self.assertEqual(self.act(assignee, str(uuid4()), "accepted").status_code, 404)
        self.assertEqual(self.event_count(draft), 0)

    def test_content_and_body_validation(self) -> None:
        _, _, assignee, _, inquiry = self.open_inquiry()
        for kind in ("edited", "denied", "question", "reply"):
            for content in (None, "", "   "):
                with self.subTest(kind=kind, content=content):
                    response = self.act(assignee, inquiry, kind, content)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["detail"]["code"], "content_required")
        for body in (
            {"kind": "closed"}, {"kind": "nope"}, {"kind": "accepted", "actor": "requester"},
            {"kind": "denied", "content": "x" * 4001}, {},
        ):
            with self.subTest(body=body):
                response = self.client.post(f"/v1/inquiries/{inquiry}/events", json=body, headers=assignee)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.event_count(inquiry), 0)
        self.assertEqual(self.client.post(f"/v1/inquiries/{inquiry}/events", json={"kind": "accepted"}).status_code, 401)

    def test_question_reply_accept_sequence_has_ordered_events(self) -> None:
        owner, owner_id, assignee, assignee_id, inquiry = self.open_inquiry()
        self.assertEqual(self.act(assignee, inquiry, "question", "Mã máy nào?").status_code, 200)
        self.assertEqual(self.act(owner, inquiry, "reply", "Máy số 3").status_code, 200)
        final = self.act(assignee, inquiry, "accepted")
        self.assertEqual(final.status_code, 200, final.text)
        for headers in (owner, assignee):
            body = self.client.get(f"/v1/inquiries/{inquiry}", headers=headers).json()
            self.assertEqual(body["status"], "answered")
            self.assertEqual([e["kind"] for e in body["events"]], ["question", "reply", "accepted"])
            self.assertEqual(
                [e["actor"]["id"] for e in body["events"]], [assignee_id, owner_id, assignee_id]
            )

    def test_concurrent_actions_let_exactly_one_succeed(self) -> None:
        _, _, _, assignee_id, inquiry = self.open_inquiry()
        for _ in range(3):
            if _:
                _, _, _, assignee_id, inquiry = self.open_inquiry()

            async def race() -> list[Any]:
                engine = create_async_engine(self.database.url, poolclass=NullPool)
                try:
                    store = InquiryStore(create_session_factory(engine))
                    return await asyncio.gather(
                        store.record_event(UUID(inquiry), UUID(assignee_id), "accepted", None),
                        store.record_event(UUID(inquiry), UUID(assignee_id), "denied", "Không"),
                        return_exceptions=True,
                    )
                finally:
                    await engine.dispose()

            results = asyncio.run(race())
            failures = [r for r in results if isinstance(r, Exception)]
            self.assertEqual(len(failures), 1, results)
            self.assertIsInstance(failures[0], InvalidTransition)
            self.assertEqual(self.event_count(inquiry), 1)


if __name__ == "__main__":
    unittest.main()
