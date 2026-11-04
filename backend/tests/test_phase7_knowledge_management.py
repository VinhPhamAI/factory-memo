"""Phase 7 HTTP workflows against a disposable application database."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import mock, skipUnless
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import create_app
from backend.src.services.auth import Account, InvalidSession
from backend.src.storage.document_uploads import DocumentUploadStore
from backend.src.storage.orm import File, IngestionJob, KnowledgeSource, User
from backend.tests.test_database_migrations import DATABASE_URL, EMBEDDING_DIM, MigratedDatabaseTest


class TestAccounts:
    """Provide deterministic authentication while exercising the real API storage."""

    def __init__(self, accounts: dict[str, Account]) -> None:
        self.accounts = accounts

    async def authenticate_access(self, token: str) -> Account:
        """Return the fixture account for its bearer token."""

        if token not in self.accounts:
            raise InvalidSession()
        return self.accounts[token]


class ConcurrentUploadStore(DocumentUploadStore):
    """Let both original objects reach disk before either upload inserts its row."""

    barrier: Barrier | None = None

    def save(self, *args, **kwargs):
        """Synchronize exactly the two uploads in the concurrency check."""

        document = super().save(*args, **kwargs)
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        return document


@skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class Phase7ApiTest(MigratedDatabaseTest):
    """Exercise routes, cleanup, access control and catalog invalidation."""

    def test_rename_duplicate_remove_and_delete(self) -> None:
        """Run the management flow with two API instances and a shared database."""

        accounts = {}
        with Session(self.engine) as session, session.begin():
            for username, role in (
                ("worker", "employee"), ("other", "employee"),
                ("expert", "expert"), ("admin", "admin"),
            ):
                user = User(username=username, display_name=username, password_hash="test", role=role)
                session.add(user)
                session.flush()
                accounts[username] = Account(user.id, username, username, role)

        def headers(token: str) -> dict[str, str]:
            return {"Authorization": f"Bearer {token}"}

        admin, expert, employee, other = map(headers, ("admin", "expert", "worker", "other"))
        self.enterContext(mock.patch.dict(os.environ, {
            "APP_DATABASE_URL": self.database.url, "EMBEDDING_DIM": str(EMBEDDING_DIM),
        }))
        store = ConcurrentUploadStore(Path(self.enterContext(TemporaryDirectory())))
        auth = TestAccounts(accounts)
        api = self.enterContext(TestClient(create_app(auth_service=auth, document_uploads=store)))
        qa = self.enterContext(TestClient(create_app(auth_service=auth, document_uploads=store)))

        kb_path = "/v1/knowledge-bases/factory"
        self.assertEqual(api.post("/v1/knowledge-bases", headers=expert, json={
            "name": "factory", "title": "Factory",
        }).status_code, 201)
        self.assertEqual(qa.get(kb_path, headers=employee).json()["created_by"], "expert")
        self.assertEqual(api.patch(kb_path, headers=expert, json={"title": "Line A"}).status_code, 200)
        self.assertEqual(qa.get("/v1/knowledge-bases", headers=employee)
                         .json()["knowledge_bases"][0]["title"], "Line A")

        def upload(filename: str):
            return api.post("/v1/documents/factory/uploads", headers=employee,
                            files={"file": (filename, b"Pump inspection", "text/plain")})

        store.barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.map(upload, ("manual.txt", "renamed.txt"))
        store.barrier = None
        self.assertEqual(sorted((first.status_code, second.status_code)), [200, 201])
        created, duplicate = (first, second) if first.status_code == 201 else (second, first)
        original = created.json()
        self.assertEqual(duplicate.json(), {**original, "duplicate": True,
                                           "uploaded_at": duplicate.json()["uploaded_at"]})
        self.assertEqual(len(list(store.root.glob("factory/*/content"))), 1)
        self.assertEqual(api.get(kb_path, headers=employee).json()["source_counts"]["received"], 1)

        inventory = "/v1/knowledge-management/factory/sources"
        source = f"{inventory}/upload/{original['id']}"
        self.assertEqual(api.delete(source, headers=other).status_code, 403)
        self.assertEqual(api.get(f"{source}/original", headers=employee).content, b"Pump inspection")
        self.assertEqual(api.post(f"{source}/convert", headers=employee).status_code, 202)
        jobs = api.app.state.knowledge_management.jobs
        running = api.portal.call(jobs.claim, "convert", "test-worker")
        self.assertEqual(api.delete(source, headers=employee).status_code, 409)
        api.portal.call(jobs.release, running, "test-worker")
        self.assertEqual(api.delete(source, headers=employee).status_code, 204)
        self.assertEqual(api.get(f"{source}/original", headers=employee).status_code, 404)
        self.assertEqual(api.get(inventory, headers=employee).json()["sources"], [])
        removed = api.get(inventory, headers=employee, params={"include_removed": "true"}).json()["sources"]
        self.assertIsNotNone(removed[0]["removed_at"])
        self.assertTrue((store.root / original["storage_key"]).is_file())
        with Session(self.engine) as session:
            row = session.get(KnowledgeSource, UUID(original["id"]))
            self.assertIsNotNone(session.get(File, row.file_id).deleted_at)
            self.assertEqual(session.scalar(select(IngestionJob.error).where(
                IngestionJob.source_id == row.id,
            )), "SOURCE_REMOVED")

        reupload = upload("manual.txt")
        self.assertEqual(reupload.status_code, 201, reupload.text)
        self.assertNotEqual(reupload.json()["id"], original["id"])
        self.assertEqual(api.delete(kb_path, headers=expert).status_code, 403)
        self.assertEqual(api.delete(kb_path, headers=admin).status_code, 204)
        self.assertEqual(api.delete(kb_path, headers=admin).status_code, 404)
        self.assertEqual(qa.get(kb_path, headers=employee).status_code, 404)
        self.assertEqual(qa.get("/v1/knowledge-agent/knowledge-bases", headers=employee)
                         .json()["knowledge_bases"], [])
        self.assertEqual(qa.post("/v1/knowledge-agent/factory/stream", headers=employee,
                                 json={"query": "Pump?", "thread_id": "phase7"}).status_code, 404)
        self.assertEqual(api.post("/v1/knowledge-bases", headers=expert, json={
            "name": "factory", "title": "Name is reserved",
        }).status_code, 409)
