"""Integration tests against a real PostgreSQL + pgvector database.

Run with the compose database up:
    docker compose -f deployments/docker-compose.yml up -d
    TEST_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent \
        uv run python -m unittest backend.tests.test_knowledge_store_integration -v

Tables are created in a throwaway schema, so existing data is untouched.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlalchemy import text

from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage import (
    ConversationStatus,
    KnowledgeRecord,
    KnowledgeStore,
    StorageError,
    build_knowledge_filter,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
DIM = 16


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class KnowledgeStoreIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.schema = f"test_{uuid4().hex[:12]}"
        self.store = KnowledgeStore.from_url(
            DATABASE_URL,
            DeterministicFakeEmbedding(size=DIM),
            DIM,
            schema=self.schema,
        )
        async with self.store.engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA {self.schema}"))

    async def asyncTearDown(self) -> None:
        async with self.store.engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        await self.store.aclose()

    async def _seed(self) -> tuple[UUID, dict[str, KnowledgeRecord]]:
        await self.store.init_schema()
        conversation = await self.store.add_conversation(
            "Expert: on M01 clean the rear sensor lens first.",
            stated_by="Maintenance Expert",
            metadata={"case_id": "1"},
        )
        records = {
            "m01": KnowledgeRecord(
                subject="rear photoelectric sensor",
                claim="Clean the lens before replacing the sensor.",
                context={"machine_id": "M01", "condition": {"environment": "oil mist"}},
                type=KnowledgeType.PROCEDURE,
                evidence_id=conversation.id,
                stated_by="Maintenance Expert",
            ),
            "m02": KnowledgeRecord(
                subject="rear photoelectric sensor",
                claim="Check the connector before anything else.",
                context={"machine_id": "M02"},
                type=KnowledgeType.HEURISTIC,
                evidence_id=conversation.id,
            ),
            "general": KnowledgeRecord(
                subject="photoelectric sensor",
                claim="Operating temperature must stay below 60C.",
                type=KnowledgeType.FACT,
                evidence_id=conversation.id,
            ),
            "expired": KnowledgeRecord(
                subject="photoelectric sensor",
                claim="Use lens cleaner type A.",
                type=KnowledgeType.RULE,
                evidence_id=conversation.id,
                valid_from=date(2024, 1, 1),
                valid_to=date(2025, 1, 1),
            ),
        }
        records["general"].conflict_with = [records["expired"].id]
        await self.store.add_knowledge(list(records.values()))
        return conversation.id, records

    async def test_init_schema_is_idempotent(self) -> None:
        await self.store.init_schema()
        await self.store.init_schema()

    async def test_dimension_mismatch_is_detected(self) -> None:
        await self.store.init_schema()
        other = KnowledgeStore(
            self.store.engine, DeterministicFakeEmbedding(size=8), 8, schema=self.schema
        )
        with self.assertRaises(StorageError):
            await other.init_schema()

    async def test_conversation_round_trip(self) -> None:
        await self.store.init_schema()
        conversation = await self.store.add_conversation(
            "hello", stated_by="Expert", metadata={"case_id": "7"}
        )
        self.assertEqual(conversation.status, ConversationStatus.PENDING)
        await self.store.mark_conversation(
            conversation.id, ConversationStatus.FAILED, error="boom"
        )
        stored = await self.store.get_conversation(conversation.id)
        self.assertEqual(stored.metadata, {"case_id": "7"})
        self.assertEqual(stored.status, ConversationStatus.FAILED)
        self.assertEqual(stored.error, "boom")
        self.assertIsNone(await self.store.get_conversation(uuid4()))

    async def test_records_round_trip_with_native_types(self) -> None:
        evidence_id, records = await self._seed()
        wanted = [records["general"].id, uuid4(), records["m01"].id]
        general, m01 = await self.store.get_knowledge(wanted)

        self.assertEqual(general.id, records["general"].id)
        self.assertEqual(general.conflict_with, [records["expired"].id])
        self.assertEqual(m01.context, records["m01"].context)
        self.assertEqual(m01.evidence_id, evidence_id)
        self.assertEqual(m01.type, KnowledgeType.PROCEDURE)
        self.assertEqual(m01.conflict_with, [])
        self.assertIsNone(m01.embedding)
        (expired,) = await self.store.get_knowledge([records["expired"].id])
        self.assertEqual((expired.valid_from, expired.valid_to), (date(2024, 1, 1), date(2025, 1, 1)))

    async def test_search_filters(self) -> None:
        _, records = await self._seed()

        async def ids(**filters) -> set[UUID]:
            hits = await self.store.search_knowledge(
                "sensor", filters=build_knowledge_filter(**filters), k=10
            )
            for _, distance in hits:
                self.assertIsInstance(distance, float)
            return {record.id for record, _ in hits}

        everything = {record.id for record in records.values()}
        self.assertEqual(await ids(), everything)
        self.assertEqual(await ids(machine_id="M01"), everything - {records["m02"].id})
        self.assertEqual(
            await ids(valid_on=date(2026, 9, 25)), everything - {records["expired"].id}
        )
        self.assertEqual(await ids(knowledge_type="heuristic"), {records["m02"].id})
        self.assertEqual(
            await ids(subject="REAR photo"), {records["m01"].id, records["m02"].id}
        )
        self.assertEqual(
            await ids(machine_id="M01", subject="rear", valid_on=date(2026, 9, 25)),
            {records["m01"].id},
        )

    async def test_unknown_evidence_is_rejected(self) -> None:
        await self.store.init_schema()
        record = KnowledgeRecord(
            subject="s", claim="c", type=KnowledgeType.FACT, evidence_id=uuid4()
        )
        with self.assertRaises(StorageError):
            await self.store.add_knowledge([record])


if __name__ == "__main__":
    unittest.main()
