"""Unit tests for knowledge storage models and retrieval filters (no database)."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.documents import Document

from backend.src.services.knowledge_extraction import KnowledgeItem, KnowledgeType
from backend.src.storage import KnowledgeRecord, build_knowledge_filter


def _record(**overrides) -> KnowledgeRecord:
    values = {
        "subject": "rear photoelectric sensor",
        "claim": "Oil mist on the lens causes an unstable signal; clean it before replacing.",
        "context": {"machine_id": "M01", "condition": {"environment": "oil mist"}},
        "type": KnowledgeType.HEURISTIC,
        "evidence_id": uuid4(),
        "stated_by": "Maintenance Expert",
        "created_at": datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc),
        "valid_from": date(2026, 1, 1),
        "valid_to": None,
    }
    values.update(overrides)
    return KnowledgeRecord(**values)


class BuildKnowledgeFilterTest(unittest.TestCase):
    def test_no_constraints_returns_none(self) -> None:
        self.assertIsNone(build_knowledge_filter())
        self.assertIsNone(build_knowledge_filter(subject="  ", machine_id=""))

    def test_single_constraint_is_not_wrapped(self) -> None:
        self.assertEqual(
            build_knowledge_filter(knowledge_type="rule"),
            {"type": {"$eq": "rule"}},
        )

    def test_subject_uses_escaped_case_insensitive_substring(self) -> None:
        self.assertEqual(
            build_knowledge_filter(subject=" 50%_sensor "),
            {"subject": {"$ilike": "%50\\%\\_sensor%"}},
        )

    def test_machine_filters_keep_unscoped_knowledge(self) -> None:
        self.assertEqual(
            build_knowledge_filter(machine_id="M01", machine_model="X200"),
            {
                "$and": [
                    {
                        "$or": [
                            {"context.machine_id": {"$eq": "M01"}},
                            {"context.machine_id": {"$exists": False}},
                        ]
                    },
                    {
                        "$or": [
                            {"context.machine_model": {"$eq": "X200"}},
                            {"context.machine_model": {"$exists": False}},
                        ]
                    },
                ]
            },
        )

    def test_valid_on_accepts_open_ended_ranges(self) -> None:
        day = date(2026, 9, 25)
        self.assertEqual(
            build_knowledge_filter(valid_on=day),
            {
                "$and": [
                    {"$or": [{"valid_from": {"$exists": False}}, {"valid_from": {"$lte": day}}]},
                    {"$or": [{"valid_to": {"$exists": False}}, {"valid_to": {"$gte": day}}]},
                ]
            },
        )

    def test_invalid_knowledge_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_knowledge_filter(knowledge_type="opinion")


class KnowledgeRecordTest(unittest.TestCase):
    def test_from_extracted_attaches_ingestion_metadata(self) -> None:
        evidence_id = uuid4()
        item = KnowledgeItem(
            subject="spindle bearing",
            claim="Replace when vibration exceeds 7 mm/s.",
            context={"machine_model": "X200"},
            type=KnowledgeType.RULE,
            valid_from=date(2026, 3, 1),
        )
        record = KnowledgeRecord.from_extracted(
            item, evidence_id=evidence_id, stated_by="Expert A"
        )
        self.assertIsInstance(record.id, UUID)
        self.assertEqual(record.evidence_id, evidence_id)
        self.assertEqual(record.stated_by, "Expert A")
        self.assertEqual(record.type, KnowledgeType.RULE)
        self.assertEqual(record.valid_from, date(2026, 3, 1))
        self.assertEqual(record.conflict_with, [])
        self.assertIsNotNone(record.created_at.tzinfo)
        # The record must not share the extracted item's context dict.
        record.context["machine_id"] = "M02"
        self.assertNotIn("machine_id", item.context)

    def test_embedding_text_leads_with_key_context(self) -> None:
        record = _record(
            context={"shift": "night", "condition": {"temperature": "80C"}, "machine_id": "M01"}
        )
        self.assertEqual(
            record.embedding_text(),
            "rear photoelectric sensor: Oil mist on the lens causes an unstable signal; "
            "clean it before replacing.\n"
            'Context: machine_id=M01; condition={"temperature": "80C"}; shift=night',
        )

    def test_embedding_text_without_context(self) -> None:
        record = _record(context={})
        self.assertNotIn("Context:", record.embedding_text())

    def test_embedding_is_excluded_from_dumps_and_repr(self) -> None:
        record = _record(embedding=[0.1, 0.2, 0.3])
        self.assertNotIn("embedding", record.model_dump())
        self.assertNotIn("embedding", record.model_dump_json())
        self.assertNotIn("0.1", repr(record))
        self.assertEqual(record.embedding, [0.1, 0.2, 0.3])

    def test_metadata_round_trips_through_document(self) -> None:
        conflict = uuid4()
        record = _record(conflict_with=[conflict])
        metadata = record.to_metadata()
        self.assertEqual(metadata["type"], "heuristic")
        self.assertEqual(metadata["evidence_id"], str(record.evidence_id))
        self.assertEqual(metadata["conflict_with"], "{" + str(conflict) + "}")

        restored = KnowledgeRecord.from_document(
            Document(id=str(record.id), page_content=record.claim, metadata=metadata)
        )
        self.assertEqual(restored, record)

    def test_empty_conflicts_become_empty_array_literal(self) -> None:
        record = _record()
        self.assertEqual(record.to_metadata()["conflict_with"], "{}")
        restored = KnowledgeRecord.from_document(
            Document(id=str(record.id), page_content=record.claim, metadata=record.to_metadata())
        )
        self.assertEqual(restored.conflict_with, [])

    def test_from_document_accepts_native_database_types(self) -> None:
        record = _record(conflict_with=[uuid4()])
        metadata = {
            **record.to_metadata(),
            "evidence_id": record.evidence_id,
            "conflict_with": list(record.conflict_with),
        }
        restored = KnowledgeRecord.from_document(
            Document(id=str(record.id), page_content=record.claim, metadata=metadata)
        )
        self.assertEqual(restored, record)

    def test_from_document_parses_json_context(self) -> None:
        record = _record()
        metadata = {**record.to_metadata(), "context": '{"machine_id": "M01"}'}
        restored = KnowledgeRecord.from_document(
            Document(id=str(record.id), page_content=record.claim, metadata=metadata)
        )
        self.assertEqual(restored.context, {"machine_id": "M01"})


if __name__ == "__main__":
    unittest.main()
