"""Authenticated upload storage and source-transaction behavior."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.knowledge_bases.custom import custom_spec
from backend.src.services.auth import Account, InvalidSession
from backend.src.storage.document_uploads import DocumentTooLarge, DocumentUploadStore
from backend.src.storage.repositories.base import KnowledgeStorageUnavailable


class FakeCatalog:
    def __init__(self):
        self.id = uuid4()
        self.spec = custom_spec("factory", title="Factory")

    async def refresh(self):
        pass

    def resolve(self, name):
        return (self.id, self.spec) if name == "factory" else None

    async def aclose(self):
        pass


class FakeSources:
    def __init__(self):
        self.created = []
        self.error = None

    async def create_upload(self, knowledge_base_id, **values):
        if self.error:
            raise self.error
        self.created.append((knowledge_base_id, values))
        return None, False


class FakeAuth:
    account = Account(uuid4(), "employee", "Employee", "employee")

    async def authenticate_access(self, token):
        if token != "access":
            raise InvalidSession()
        return self.account


class DocumentUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "uploads"
        self.store = DocumentUploadStore(self.root, max_bytes=32)
        self.sources = FakeSources()
        self.catalog = FakeCatalog()
        self.client = self.enterContext(
            TestClient(
                create_app(
                    knowledge_agents={},
                    kb_catalog=self.catalog,
                    document_uploads=self.store,
                    auth_service=FakeAuth(),
                    sources=self.sources,
                )
            )
        )

    @staticmethod
    def headers():
        return {"Authorization": "Bearer access"}

    def upload(self, content=b"original bytes"):
        return self.client.post(
            "/v1/documents/factory/uploads",
            headers=self.headers(),
            files={"file": ("manual.txt", content, "text/plain")},
        )

    def test_upload_creates_one_file_source_and_returns_source_id(self):
        response = self.upload()

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(len(self.sources.created), 1)
        knowledge_base_id, values = self.sources.created[0]
        self.assertEqual(knowledge_base_id, self.catalog.id)
        self.assertEqual(values["source_id"].hex, body["id"])
        self.assertEqual(values["submitted_by"], FakeAuth.account.id)
        self.assertEqual(values["file"].storage_key, body["storage_key"])
        content = self.root / body["storage_key"]
        self.assertEqual(content.read_bytes(), b"original bytes")
        self.assertFalse((content.parent / "metadata.json").exists())

    def test_failed_source_insert_removes_saved_object(self):
        self.sources.error = KnowledgeStorageUnavailable("down")

        response = self.upload()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(list((self.root / "factory").iterdir()), [])

    def test_missing_token_is_401_and_writes_nothing(self):
        response = self.client.post(
            "/v1/documents/factory/uploads",
            files={"file": ("manual.txt", b"bytes", "text/plain")},
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.root.exists())

    def test_store_rejects_empty_and_oversized_objects(self):
        with self.assertRaises(ValueError):
            self.store.save(
                "factory",
                io.BytesIO(b""),
                filename="empty.txt",
                content_type="text/plain",
                source_id=uuid4(),
            )
        with self.assertRaises(DocumentTooLarge):
            self.store.save(
                "factory",
                io.BytesIO(b"x" * 33),
                filename="large.txt",
                content_type="text/plain",
                source_id=uuid4(),
            )


if __name__ == "__main__":
    unittest.main()
