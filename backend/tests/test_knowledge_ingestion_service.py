"""Unit tests for the transcript → knowledge ingestion pipeline."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.src.services.knowledge_extraction import (
    KnowledgeExtractionError,
    KnowledgeExtractionResult,
    KnowledgeItem,
    KnowledgeType,
)
from backend.src.services.knowledge_ingestion import KnowledgeIngestionService
from backend.src.storage import ConversationStatus, RawConversation


class FakeExtractor:
    def __init__(self, result: KnowledgeExtractionResult | Exception):
        self.result = result
        self.transcripts: list[str] = []

    def extract(self, raw_conversation: str) -> KnowledgeExtractionResult:
        self.transcripts.append(raw_conversation)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeStore:
    def __init__(self, *, fail_on_write: bool = False):
        self.fail_on_write = fail_on_write
        self.conversations: dict = {}
        self.knowledge: list = []

    async def add_conversation(self, transcript, *, stated_by=None, metadata=None):
        conversation = RawConversation(
            id=uuid4(),
            transcript=transcript,
            stated_by=stated_by,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        self.conversations[conversation.id] = conversation
        return conversation

    async def mark_conversation(self, conversation_id, status, *, error=None):
        conversation = self.conversations[conversation_id]
        self.conversations[conversation_id] = conversation.model_copy(
            update={"status": status, "error": error}
        )

    async def add_knowledge(self, records):
        if self.fail_on_write:
            raise RuntimeError("database down")
        self.knowledge.extend(records)
        return [record.id for record in records]


EXTRACTED = KnowledgeExtractionResult(
    knowledge_items=[
        KnowledgeItem(
            subject="rear photoelectric sensor",
            claim="Clean the lens before replacing the sensor.",
            context={"machine_id": "M01"},
            type=KnowledgeType.PROCEDURE,
        ),
        KnowledgeItem(
            subject="rear photoelectric sensor",
            claim="Oil mist builds up on the lens after a few weeks.",
            context={"machine_id": "M01"},
            type=KnowledgeType.FACT,
        ),
    ]
)


class KnowledgeIngestionServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_links_records_to_evidence(self) -> None:
        store = FakeStore()
        service = KnowledgeIngestionService(FakeExtractor(EXTRACTED), store)

        result = await service.ingest(
            "  Expert: clean the lens first.  ",
            stated_by="Maintenance Expert",
            metadata={"case_id": "1"},
        )

        conversation = store.conversations[result.evidence_id]
        self.assertEqual(conversation.status, ConversationStatus.PROCESSED)
        self.assertEqual(conversation.transcript, "Expert: clean the lens first.")
        self.assertEqual(conversation.metadata, {"case_id": "1"})
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.knowledge_ids, tuple(r.id for r in store.knowledge))
        for record in store.knowledge:
            self.assertEqual(record.evidence_id, result.evidence_id)
            self.assertEqual(record.stated_by, "Maintenance Expert")

    async def test_conversation_without_knowledge_is_processed(self) -> None:
        store = FakeStore()
        service = KnowledgeIngestionService(
            FakeExtractor(KnowledgeExtractionResult()), store
        )
        result = await service.ingest("Hello, how are you?")
        self.assertEqual(result.records, ())
        self.assertEqual(
            store.conversations[result.evidence_id].status, ConversationStatus.PROCESSED
        )

    async def test_extraction_failure_marks_conversation_failed(self) -> None:
        store = FakeStore()
        service = KnowledgeIngestionService(
            FakeExtractor(KnowledgeExtractionError("bad json")), store
        )
        with self.assertRaises(KnowledgeExtractionError):
            await service.ingest("Expert: something")

        (conversation,) = store.conversations.values()
        self.assertEqual(conversation.status, ConversationStatus.FAILED)
        self.assertEqual(conversation.error, "KnowledgeExtractionError: bad json")
        self.assertEqual(store.knowledge, [])

    async def test_storage_failure_marks_conversation_failed(self) -> None:
        store = FakeStore(fail_on_write=True)
        service = KnowledgeIngestionService(FakeExtractor(EXTRACTED), store)
        with self.assertRaises(RuntimeError):
            await service.ingest("Expert: something")
        (conversation,) = store.conversations.values()
        self.assertEqual(conversation.status, ConversationStatus.FAILED)

    async def test_empty_transcript_is_rejected_before_storage(self) -> None:
        store = FakeStore()
        service = KnowledgeIngestionService(FakeExtractor(EXTRACTED), store)
        with self.assertRaises(ValueError):
            await service.ingest("   ")
        self.assertEqual(store.conversations, {})


if __name__ == "__main__":
    unittest.main()
