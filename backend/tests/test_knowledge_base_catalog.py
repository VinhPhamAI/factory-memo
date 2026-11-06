"""Catalog behavior after moving knowledge bases to APP_DATABASE_URL."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.api.knowledge_bases import KnowledgeBaseCatalog, KnowledgeBaseConflict
from backend.app import create_app
from backend.src.services.auth import Account, InvalidSession
from backend.src.storage.repositories.knowledge_bases import (
    KnowledgeBaseDetail, KnowledgeBaseEntry,
    KnowledgeBaseLimitReached,
    KnowledgeBaseNameTaken,
)
from backend.src.storage.orm import User
from backend.tests.test_database_migrations import DATABASE_URL, EMBEDDING_DIM, MigratedDatabaseTest
from unittest import mock
import os


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


    async def detail(self, name):
        row = self.rows.get(name)
        if row is None:
            return None
        return KnowledgeBaseDetail(
            row.name, row.title, row.description, row.template, "expert",
            row.created_at, row.created_at, {}, {},
        )

    async def rename(self, name, *, title, description):
        row = self.rows.get(name)
        if row is None:
            return None
        self.rows[name] = replace(
            row, title=row.title if title is None else title,
            description=row.description if description is None else description,
        )
        return self.rows[name], await self.detail(name)

    async def delete(self, name):
        return self.rows.pop(name, None) is not None

    async def set_default(self, name):
        if name not in self.rows:
            return False
        self.rows = {key: replace(value, is_default=(key == name)) for key, value in self.rows.items()}
        return True


class Listener:
    def __init__(self):
        self.names: list[str] = []
        self.removed: list[str] = []

    def add(self, spec):
        self.names.append(spec.name)

    def remove(self, name):
        self.removed.append(name)


class FakeAuth:
    accounts = {
        "expert": Account(uuid4(), "expert", "Expert", "expert"),
        "employee": Account(uuid4(), "employee", "Employee", "employee"),
        "admin": Account(uuid4(), "admin", "Admin", "admin"),
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


    async def test_refresh_updates_and_removes_kbs_in_other_processes(self):
        repository = FakeRepository()
        writer = KnowledgeBaseCatalog(repository)
        listener = Listener()
        reader = KnowledgeBaseCatalog(repository, listeners=[listener])
        await writer.create(name="may_ep", title="Máy ép", created_by=uuid4())
        await reader.refresh()
        await writer.rename("may_ep", title="Renamed", description=None)
        await reader.refresh()
        self.assertEqual(reader.resolve("may_ep")[1].title, "Renamed")
        await writer.delete("may_ep")
        await reader.refresh()
        self.assertIsNone(reader.resolve("may_ep"))
        self.assertEqual(listener.removed, ["may_ep"])


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

    def test_detail_and_patch_permissions(self):
        path = "/v1/knowledge-bases/may_ep"
        self.assertEqual(self.client.get(path, headers=bearer("employee")).status_code, 404)
        self.assertEqual(self.client.post(
            "/v1/knowledge-bases", headers=bearer("expert"),
            json={"name": "may_ep", "title": "Máy ép"},
        ).status_code, 201)
        self.assertEqual(self.client.patch(
            path, headers=bearer("employee"), json={"title": "Renamed"},
        ).status_code, 403)
        self.assertEqual(self.client.patch(
            path, headers=bearer("expert"), json={"name": "other"},
        ).status_code, 422)
        self.assertEqual(self.client.patch(
            path, headers=bearer("expert"), json={"title": "Renamed"},
        ).json()["title"], "Renamed")
        self.assertEqual(self.client.get(
            "/v1/knowledge-bases", headers=bearer("employee"),
        ).json()["knowledge_bases"][0]["title"], "Renamed")

    def test_only_admin_can_change_the_default(self):
        for name in ("may_ep", "pump"):
            self.assertEqual(self.client.post("/v1/knowledge-bases", headers=bearer("expert"), json={"name": name, "title": name}).status_code, 201)
        path = "/v1/knowledge-bases/may_ep/default"
        self.assertEqual(self.client.put(path, headers=bearer("employee")).status_code, 403)
        self.assertEqual(self.client.put(path, headers=bearer("expert")).status_code, 403)
        self.assertEqual(self.client.put(path, headers=bearer("admin")).status_code, 204)
        items = self.client.get("/v1/knowledge-bases", headers=bearer("employee")).json()["knowledge_bases"]
        self.assertEqual([item["name"] for item in items if item["is_default"]], ["may_ep"])
        self.assertEqual(self.client.put("/v1/knowledge-bases/pump/default", headers=bearer("admin")).status_code, 204)
        items = self.client.get("/v1/knowledge-bases", headers=bearer("employee")).json()["knowledge_bases"]
        self.assertEqual([item["name"] for item in items if item["is_default"]], ["pump"])


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class DefaultCatalogDatabaseTest(MigratedDatabaseTest):
    def test_default_switch_and_delete_protection(self):
        """Persist default changes across requests and protect the active default."""
        with Session(self.engine) as session, session.begin():
            user = User(username="default_admin", display_name="Admin", password_hash="x", role="admin")
            session.add(user)
            session.flush()
            account = Account(user.id, user.username, user.display_name, user.role)
        class AdminAuth:
            async def authenticate_access(self, token):
                return account
        self.enterContext(mock.patch.dict(os.environ, {"APP_DATABASE_URL": self.database.url, "EMBEDDING_DIM": str(EMBEDDING_DIM)}))
        with TestClient(create_app(auth_service=AdminAuth())) as client:
            headers = bearer("admin")
            for name in ("alpha", "beta"):
                response = client.post("/v1/knowledge-bases", headers=headers, json={"name": name, "title": name})
                self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(client.put("/v1/knowledge-bases/alpha/default", headers=headers).status_code, 204)
            self.assertEqual(client.delete("/v1/knowledge-bases/alpha", headers=headers).status_code, 409)
            self.assertEqual(client.put("/v1/knowledge-bases/beta/default", headers=headers).status_code, 204)
            self.assertEqual(client.delete("/v1/knowledge-bases/alpha", headers=headers).status_code, 204)
            items = client.get("/v1/knowledge-bases", headers=headers).json()["knowledge_bases"]
            self.assertEqual([(item["name"], item["is_default"]) for item in items], [("beta", True)])


if __name__ == "__main__":
    unittest.main()
