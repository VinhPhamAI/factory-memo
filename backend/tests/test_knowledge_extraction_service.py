"""Unit tests for the structured knowledge extraction service."""

from __future__ import annotations

import csv
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_CSV = (
    PROJECT_ROOT
    / "data"
    / "expert_knowledge_extraction_benchmark_20_conversations_en.csv"
)
sys.path.insert(0, str(PROJECT_ROOT))

from backend.src.services.knowledge_extraction import (
    KnowledgeExtractionError,
    KnowledgeExtractionService,
    KnowledgeType,
)


class FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.last_prompt: str | None = None

    def invoke(self, prompt: str) -> SimpleNamespace:
        self.last_prompt = prompt
        return SimpleNamespace(content=self.response)


class FailingLLM:
    def invoke(self, prompt: str) -> SimpleNamespace:
        raise ConnectionError("endpoint unavailable")


class KnowledgeExtractionServiceTests(unittest.TestCase):
    def test_extracts_validated_items_from_benchmark_conversation(self) -> None:
        with BENCHMARK_CSV.open(newline="", encoding="utf-8-sig") as csv_file:
            conversation = next(csv.DictReader(csv_file))["raw_conversation"]

        client = FakeLLM(
            """```json
            {
              "knowledge_items": [{
                "subject": "rear photoelectric sensor",
                "claim": "Clean oil buildup from the lens before replacing the sensor.",
                "context": {"machine_id": "M01", "condition": "unstable signal"},
                "type": "rule",
                "valid_from": null,
                "valid_to": null
              }]
            }
            ```"""
        )

        result = KnowledgeExtractionService(client).extract(conversation)

        self.assertEqual(len(result.knowledge_items), 1)
        self.assertEqual(result.knowledge_items[0].type, KnowledgeType.RULE)
        self.assertEqual(result.knowledge_items[0].context["machine_id"], "M01")
        self.assertIn(conversation, client.last_prompt or "")
        self.assertNotIn("{RAW_CONVERSATION}", client.last_prompt or "")

    def test_rejects_empty_conversation_before_calling_llm(self) -> None:
        client = FakeLLM('{"knowledge_items": []}')

        with self.assertRaisesRegex(ValueError, "must not be empty"):
            KnowledgeExtractionService(client).extract("  ")

        self.assertIsNone(client.last_prompt)

    def test_rejects_invalid_llm_json(self) -> None:
        client = FakeLLM("not JSON")

        with self.assertRaises(KnowledgeExtractionError):
            KnowledgeExtractionService(client).extract("Expert: Check the sensor.")

    def test_wraps_llm_client_errors(self) -> None:
        with self.assertRaisesRegex(
            KnowledgeExtractionError, "LLM request failed: endpoint unavailable"
        ):
            KnowledgeExtractionService(FailingLLM()).extract(
                "Expert: Check the sensor."
            )


if __name__ == "__main__":
    unittest.main()
