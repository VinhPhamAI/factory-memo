"""Knowledge base registry, spec validation, and per-KB database isolation.

The database tests need a server and are skipped unless TEST_DATABASE_URL is
set; they create and drop throwaway kb_test_* databases.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlalchemy import text

from backend.knowledge_bases import KNOWLEDGE_BASES, KnowledgeBaseSpec, get_kb
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage import KnowledgeRecord, KnowledgeStore
from backend.src.storage.databases import (
    database_exists,
    database_url,
    drop_database,
    ensure_database,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
DIM = 8


def _spec(name: str, **fields) -> KnowledgeBaseSpec:
    return KnowledgeBaseSpec(
        name=name,
        extract_prompt="Extract:\n{RAW_CONVERSATION}",
        agent_prompt="Answer from the knowledge base.",
        **fields,
    )


class KnowledgeBaseSpecTest(unittest.TestCase):
    def test_maintenance_is_registered_with_its_prompts(self) -> None:
        spec = get_kb("maintenance")
        self.assertIs(KNOWLEDGE_BASES["maintenance"], spec)
        self.assertEqual(spec.database, "kb_maintenance")
        self.assertIn("{RAW_CONVERSATION}", spec.extract_prompt)
        self.assertIn("maintenance knowledge assistant", spec.agent_prompt)
        self.assertEqual(spec.text_search_config, "english")

    def test_unknown_knowledge_base_lists_available_ones(self) -> None:
        with self.assertRaisesRegex(ValueError, "available: .*maintenance"):
            get_kb("missing")

    def test_rejects_names_that_are_not_plain_identifiers(self) -> None:
        for name in ("", "Maintenance", "1kb", "kb-name", 'kb"; drop'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _spec(name)

    def test_rejects_extract_prompt_without_placeholder(self) -> None:
        with self.assertRaisesRegex(ValueError, "RAW_CONVERSATION"):
            KnowledgeBaseSpec(name="x", extract_prompt="no slot", agent_prompt="a")

    def test_database_url_keeps_server_and_credentials(self) -> None:
        self.assertEqual(
            database_url("postgresql+psycopg://u:p@localhost:5434/voice_agent", "kb_x"),
            "postgresql+psycopg://u:p@localhost:5434/kb_x",
        )


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class KnowledgeBaseDatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        suffix = uuid4().hex[:10]
        self.first = _spec(f"test_{suffix}_a")
        self.second = _spec(f"test_{suffix}_b", text_search_config="simple")
        self.env = mock.patch.dict(
            os.environ,
            {"DATABASE_URL": DATABASE_URL, "EMBEDDING_DIM": str(DIM)},
        )
        self.env.start()

    async def asyncTearDown(self) -> None:
        for spec in (self.first, self.second):
            await drop_database(DATABASE_URL, spec.database)
        self.env.stop()

    async def _open(self, spec: KnowledgeBaseSpec) -> KnowledgeStore:
        return await KnowledgeStore.for_kb(
            spec, embeddings=DeterministicFakeEmbedding(size=DIM)
        )

    async def test_ensure_and_drop_database(self) -> None:
        name = self.first.database
        self.assertFalse(await database_exists(DATABASE_URL, name))
        await ensure_database(DATABASE_URL, name)
        await ensure_database(DATABASE_URL, name)  # idempotent
        self.assertTrue(await database_exists(DATABASE_URL, name))
        self.assertTrue(await drop_database(DATABASE_URL, name))
        self.assertFalse(await drop_database(DATABASE_URL, name))

    async def test_knowledge_bases_are_isolated(self) -> None:
        first = await self._open(self.first)
        second = await self._open(self.second)
        try:
            conversation = await first.add_conversation("Expert: clean the M01 lens.")
            await first.add_knowledge(
                [
                    KnowledgeRecord(
                        subject="rear sensor",
                        claim="Clean the lens before replacing the sensor.",
                        type=KnowledgeType.RULE,
                        evidence_id=conversation.id,
                    )
                ]
            )

            self.assertEqual(len(await first.search_knowledge("lens")), 1)
            self.assertEqual(await second.search_knowledge("lens"), [])
            self.assertIsNone(await second.get_conversation(conversation.id))

            async with second.engine.connect() as conn:
                database = await conn.scalar(text("SELECT current_database()"))
                expression = await conn.scalar(
                    text(
                        "SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef AS d "
                        "JOIN pg_attribute AS a "
                        "ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
                        "WHERE a.attrelid = 'knowledge_items'::regclass "
                        "AND a.attname = 'search_vector'"
                    )
                )
            self.assertEqual(database, self.second.database)
            self.assertIn("'simple'::regconfig", expression)
        finally:
            await first.aclose()
            await second.aclose()


if __name__ == "__main__":
    unittest.main()
