"""Knowledge bases created at runtime: catalog, API and registry table.

The registry integration test needs TEST_DATABASE_URL and uses a throwaway
schema, so an existing registry is untouched.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.knowledge_bases import (
    KnowledgeBaseCatalog,
    KnowledgeBaseConflict,
    KnowledgeBaseLimit,
)
from backend.api.voice_capture import VoiceCaptureService
from backend.app import create_app
from backend.knowledge_bases import KNOWLEDGE_BASES, Retrieval, get_kb
from backend.knowledge_bases.custom import custom_spec
from backend.src.storage import StorageError
from backend.src.storage.kb_registry import (
    KnowledgeBaseExistsError,
    KnowledgeBaseRegistry,
    RegisteredKnowledgeBase,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class FakeRegistry:
    def __init__(self, rows=(), *, fail: bool = False):
        self.rows = {row.name: row for row in rows}
        self.fail = fail

    async def list(self):
        return list(self.rows.values())

    async def add(self, *, name, title, description, template):
        if self.fail:
            raise StorageError("registry down")
        if name in self.rows:
            raise KnowledgeBaseExistsError(name)
        self.rows[name] = RegisteredKnowledgeBase(
            name, title, description, template, datetime.now(timezone.utc)
        )
        return self.rows[name]

    async def aclose(self):
        pass


class FakeAdmin:
    def __init__(self, existing=()):
        self.databases = set(existing)
        self.dropped: list[str] = []

    async def exists(self, database):
        return database in self.databases

    async def create(self, database):
        self.databases.add(database)

    async def drop(self, database):
        self.databases.discard(database)
        self.dropped.append(database)


class Listener:
    def __init__(self):
        self.specs = []

    def add(self, spec):
        self.specs.append(spec)


def _row(name, title="T", template="general"):
    return RegisteredKnowledgeBase(name, title, "", template, datetime(2026, 10, 16, tzinfo=timezone.utc))


class CustomSpecTest(unittest.TestCase):
    def test_copies_the_template_policy_with_its_own_database(self) -> None:
        spec = custom_spec("may_ep", title="  Máy  ép ", description=" Lỗi máy ép ")
        general = get_kb("general")
        self.assertEqual((spec.title, spec.description, spec.database),
                         ("Máy ép", "Lỗi máy ép", "kb_may_ep"))
        self.assertEqual(spec.extract_prompt, general.extract_prompt)
        self.assertIs(spec.extraction_output_model, general.extraction_output_model)
        for bad in ({"name": "May"}, {"title": " "}, {"template": "industrial_qa"},
                    {"description": "x" * 501}):
            with self.subTest(bad), self.assertRaises(ValueError):
                custom_spec(**{"name": "may_ep", "title": "Máy ép", **bad})


class LimitSettingTest(unittest.TestCase):
    def test_empty_setting_means_the_default(self) -> None:
        from unittest import mock

        from backend.api.knowledge_bases import max_created_from_env

        for value, expected in (("", 50), ("3", 3), ("-1", 0)):
            with self.subTest(value), mock.patch.dict(os.environ, {"KB_MAX_CREATED": value}):
                self.assertEqual(max_created_from_env(), expected)


class CatalogTest(unittest.IsolatedAsyncioTestCase):
    def catalog(self, registry=None, admin=None, **kwargs):
        self.listener = Listener()
        self.registry = registry or FakeRegistry()
        self.admin = admin or FakeAdmin()
        return KnowledgeBaseCatalog(
            KNOWLEDGE_BASES, listeners=[self.listener],
            registry_factory=lambda: self.registry, database_admin=self.admin, **kwargs,
        )

    async def test_create_makes_the_database_registers_and_serves_it(self) -> None:
        catalog = self.catalog()
        created = await catalog.create(name="may_ep", title="Máy ép", description="Lỗi máy ép")
        self.assertEqual(created["title"], "Máy ép")
        self.assertFalse(created["builtin"])
        self.assertTrue(created["capture"])
        self.assertIn("kb_may_ep", self.admin.databases)
        self.assertIn("may_ep", self.registry.rows)
        self.assertEqual([spec.name for spec in self.listener.specs], ["may_ep"])
        names = [item["name"] for item in catalog.items()]
        self.assertEqual(names[-1], "may_ep")
        self.assertTrue({"general", "industrial_qa"} <= set(names))

    async def test_refuses_taken_names_and_unregistered_databases(self) -> None:
        catalog = self.catalog(admin=FakeAdmin({"kb_old_data"}))
        await catalog.create(name="may_ep", title="Máy ép")
        for name in ("general", "may_ep", "old_data"):
            with self.subTest(name), self.assertRaises(KnowledgeBaseConflict):
                await catalog.create(name=name, title="x")
        self.assertEqual(self.admin.dropped, [])
        with self.assertRaises(ValueError):
            await catalog.create(name="Máy ép", title="x")

    async def test_limit_and_registry_failure_leave_nothing_behind(self) -> None:
        catalog = self.catalog(max_created=1)
        await catalog.create(name="a1", title="A")
        with self.assertRaises(KnowledgeBaseLimit):
            await catalog.create(name="a2", title="A")

        catalog = self.catalog(registry=FakeRegistry(fail=True))
        with self.assertRaises(StorageError):
            await catalog.create(name="a3", title="A")
        self.assertEqual(self.admin.dropped, ["kb_a3"])
        self.assertEqual(self.listener.specs, [])
        self.assertNotIn("a3", [item["name"] for item in catalog.items()])

    async def test_load_rebuilds_registered_knowledge_bases_once(self) -> None:
        registry = FakeRegistry([_row("may_ep", "Máy ép"), _row("general"),
                                 _row("gone", template="removed")])
        catalog = self.catalog(registry=registry)
        await catalog.ensure_loaded()
        await catalog.ensure_loaded()
        self.assertEqual([spec.name for spec in self.listener.specs], ["may_ep"])
        general = next(item for item in catalog.items() if item["name"] == "general")
        self.assertTrue(general["builtin"])


class CatalogApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.capture = VoiceCaptureService(
            {
                "general": get_kb("general"),
                "documents_test": replace(get_kb("general"), name="documents_test", retrieval=Retrieval.DOCUMENTS),
            },
            audio_dir=Path(self.tmp.name),
        )
        self.catalog = KnowledgeBaseCatalog(
            KNOWLEDGE_BASES, listeners=[self.capture],
            registry_factory=FakeRegistry, database_admin=FakeAdmin(),
        )
        self.client = TestClient(create_app(voice_capture=self.capture, kb_catalog=self.catalog))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_created_knowledge_base_is_listed_and_accepts_capture(self) -> None:
        response = self.client.post("/v1/knowledge-bases", json={
            "name": "may_ep", "title": "Máy ép", "description": "Lỗi máy ép thủy lực",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "may_ep")
        listed = self.client.get("/v1/knowledge-bases").json()
        self.assertEqual(listed["templates"][0]["name"], "general")
        self.assertIn("may_ep", [item["name"] for item in listed["knowledge_bases"]])

        capture = self.client.get("/v1/voice/knowledge-bases").json()["knowledge_bases"]
        self.assertEqual([item["name"] for item in capture], ["general", "may_ep"])
        self.assertEqual(capture[1]["title"], "Máy ép")

        self.assertEqual(self.client.post("/v1/knowledge-bases", json={
            "name": "may_ep", "title": "Khác"}).status_code, 409)
        self.assertEqual(self.client.post("/v1/knowledge-bases", json={
            "name": "May Ep", "title": "Khác"}).status_code, 422)
        self.assertEqual(self.client.post("/v1/knowledge-bases", json={
            "name": "x1", "title": "Khác", "template": "industrial_qa"}).status_code, 422)


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class RegistryIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.schema = f"test_{uuid4().hex[:12]}"
        self.registry = KnowledgeBaseRegistry.from_url(DATABASE_URL, schema=self.schema)
        async with self.registry._engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))

    async def asyncTearDown(self) -> None:
        async with self.registry._engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.registry.aclose()

    async def test_rows_persist_and_names_are_unique(self) -> None:
        self.assertEqual(await self.registry.list(), [])
        added = await self.registry.add(name="may_ep", title="Máy ép", description="", template="general")
        with self.assertRaises(KnowledgeBaseExistsError):
            await self.registry.add(name="may_ep", title="x", description="", template="general")
        reopened = KnowledgeBaseRegistry.from_url(DATABASE_URL, schema=self.schema)
        try:
            self.assertEqual(await reopened.list(), [added])
        finally:
            await reopened.aclose()
