"""Catalog behavior after moving knowledge bases to APP_DATABASE_URL."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.knowledge_bases import KnowledgeBaseCatalog, KnowledgeBaseConflict
from backend.app import create_app
from backend.src.services.auth import Account, InvalidSession
from backend.src.storage.repositories.knowledge_bases import (
    KnowledgeBaseEntry,
    KnowledgeBaseLimitReached,
    KnowledgeBaseNameTaken,
)


class FakeRepository:
    """In-memory repository with the same uniqueness and active-limit rules."""

    def __init__(self, rows=()):
        self.rows = {row.name: row for row in rows}

    async def list_active(self):
        return list(self.rows.values())

    async def create(self, *, name, title, description, template, created_by, limit):
        if name in self.rows:
            raise KnowledgeBaseNameTaken(name)
        if limit is not None and len(self.rows) >= limit:
            raise KnowledgeBaseLimitReached()
        row = KnowledgeBaseEntry(
            id=uuid4(),
            name=name,
            title=title,
            description=description,
            template=template,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        self.rows[name] = row
        return row


class Listener:
    def __init__(self):
        self.names: list[str] = []

    def add(self, spec):
        self.names.append(spec.name)


class FakeAuth:
    accounts = {
        "expert": Account(uuid4(), "expert", "Expert", "expert"),
        "employee": Account(uuid4(), "employee", "Employee", "employee"),
    }

    async def authenticate_access(self, token):
        try:
            return self.accounts[token]
        except KeyError as exc:
            raise InvalidSession() from exc


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class CatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_inserts_creator_and_notifies_listener(self):
        repository = FakeRepository()
        listener = Listener()
        catalog = KnowledgeBaseCatalog(repository, listeners=[listener])
        creator = uuid4()

        created = await catalog.create(
            name="may_ep", title="Máy ép", description="Lỗi máy ép", created_by=creator
        )

        self.assertEqual(repository.rows["may_ep"].created_by, creator)
        self.assertEqual(repository.rows["may_ep"].template, "general")
        self.assertEqual(listener.names, ["may_ep"])
        self.assertFalse(created["builtin"])

    async def test_reserved_and_used_names_conflict(self):
        catalog = KnowledgeBaseCatalog(FakeRepository())
        creator = uuid4()
        await catalog.create(name="may_ep", title="Máy ép", created_by=creator)
        for name in ("may_ep", "general", "industrial_qa"):
            with self.subTest(name), self.assertRaises(KnowledgeBaseConflict):
                await catalog.create(name=name, title="KB", created_by=creator)

    async def test_restart_loads_only_repository_rows(self):
        creator = uuid4()
        repository = FakeRepository()
        first = KnowledgeBaseCatalog(repository)
        await first.create(name="may_ep", title="Máy ép", created_by=creator)

        restarted = KnowledgeBaseCatalog(repository)
        await restarted.ensure_loaded()

        self.assertEqual(restarted.names(), ["may_ep"])
        self.assertNotIn("general", restarted.names())


class CatalogApiTest(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()
        catalog = KnowledgeBaseCatalog(self.repository)
        self.client = self.enterContext(
            TestClient(
                create_app(
                    knowledge_agents={},
                    kb_catalog=catalog,
                    auth_service=FakeAuth(),
                )
            )
        )

    def test_auth_roles_and_database_backed_listing(self):
        self.assertEqual(self.client.get("/v1/knowledge-bases").status_code, 401)
        denied = self.client.post(
            "/v1/knowledge-bases",
            headers=bearer("employee"),
            json={"name": "may_ep", "title": "Máy ép"},
        )
        self.assertEqual(denied.status_code, 403)

        created = self.client.post(
            "/v1/knowledge-bases",
            headers=bearer("expert"),
            json={"name": "may_ep", "title": "Máy ép"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        listing = self.client.get(
            "/v1/knowledge-bases", headers=bearer("employee")
        ).json()["knowledge_bases"]
        self.assertEqual([item["name"] for item in listing], ["may_ep"])
        self.assertTrue(all(item["builtin"] is False for item in listing))


if __name__ == "__main__":
    unittest.main()
