"""Unit tests for the general-purpose knowledge base (no LLM or database)."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.knowledge_bases import KNOWLEDGE_BASES, Retrieval, get_kb
from backend.knowledge_bases.general.schema import GeneralExtraction
from backend.src.agents.agent.tools import _record_payload
from backend.src.services.knowledge_extraction import (
    RAW_CONVERSATION_PLACEHOLDER,
    KnowledgeExtractionError,
    KnowledgeExtractionService,
    KnowledgeType,
)
from backend.src.services.knowledge_verification import KnowledgeVerifier
from backend.src.storage import KnowledgeRecord, KnowledgeSearchHit


GENERAL = get_kb("general")
TRANSCRIPT = """[T01] Lan: Our nightly export job failed again on the billing server.
[T02] Minh: I think the disk filled up, but I have not checked yet.
[T03] Lan: It was the disk. We cleared old logs and the job ran fine after that.
[T04] Minh: From now on, never run the export while the backup is running.
[T05] Lan: To rerun it: stop the scheduler, clear /var/log/export, then start the job."""


def _item(**fields) -> dict:
    return {
        "key": f"item-{uuid4().hex[:6]}", "type": "fact", "subject": "export job", "claim": "A claim.", "steps": None,
        "certainty": "stated", "applies_to": None, "condition": None, "speaker": None,
        "quotes": [{"quote": "It was the disk."}], "valid_from": None, "valid_to": None,
        "based_on": [], **fields,
    }


EXTRACTION = {"items": [
    _item(
        key="case-export-disk-full", type="case", subject="nightly export job",
        claim="The nightly export job on the billing server failed because the disk was full; "
              "clearing old logs fixed it.",
        applies_to="billing server", speaker="Lan",
        quotes=[{"turn_id": "T01", "quote": "nightly export job failed again"},
                {"turn_id": "T03", "quote": "It was the disk."}],
    ),
    _item(
        type="rule", subject="nightly export job",
        claim="Never run the export while the backup is running.", speaker="Minh",
        quotes=[{"turn_id": "T04", "quote": "never run the export while the backup is running"}],
        based_on=["case-export-disk-full"],
    ),
    _item(
        type="procedure", subject="nightly export job",
        claim="How to rerun the export job.",
        steps=["Stop the scheduler.", "Clear /var/log/export.", "Start the job."],
        quotes=[{"turn_id": "T05", "quote": "stop the scheduler, clear /var/log/export"}],
    ),
]}


class FakeLLM:
    """Answers with each payload in turn, repeating the last one."""

    def __init__(self, *payloads: dict):
        self.responses = [json.dumps(payload) for payload in payloads]
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        self.prompt = prompt
        return SimpleNamespace(content=self.responses[min(len(self.prompts), len(self.responses)) - 1])


def _extract(payload: dict, *, client: FakeLLM | None = None, validation_retries: int = 0):
    service = KnowledgeExtractionService(
        client or FakeLLM(payload), GENERAL.extract_prompt,
        output_model=GENERAL.extraction_output_model, validation_retries=validation_retries,
    )
    return service.extract(TRANSCRIPT)


class GeneralSpecTests(unittest.TestCase):
    def test_is_registered_as_a_knowledge_retrieval_agent(self) -> None:
        self.assertIs(KNOWLEDGE_BASES["general"], GENERAL)
        self.assertEqual(GENERAL.database, "kb_general")
        self.assertIs(GENERAL.retrieval, Retrieval.KNOWLEDGE)
        self.assertIs(GENERAL.extraction_output_model, GeneralExtraction)
        self.assertIn(RAW_CONVERSATION_PLACEHOLDER, GENERAL.extract_prompt)
        self.assertIn("[K:<knowledge_id>]", GENERAL.agent_prompt)
        self.assertTrue(GENERAL.embedding_query_instruction)
        self.assertEqual(GENERAL.trace_context_keys, ("applies_to", "condition"))

    def test_output_schema_lists_every_item_field(self) -> None:
        schema = KnowledgeExtractionService.output_schema(GeneralExtraction)
        item = schema["$defs"]["GeneralItem"]
        self.assertEqual(item["additionalProperties"], False)
        self.assertIn("certainty", item["required"])
        self.assertIn("quotes", item["required"])


class GeneralExtractionTests(unittest.TestCase):
    def test_maps_items_onto_shared_knowledge_items(self) -> None:
        case, rule, procedure = _extract(EXTRACTION).knowledge_items

        self.assertEqual(case.type, KnowledgeType.CASE)
        self.assertEqual(case.context["certainty"], "stated")
        self.assertEqual(case.context["applies_to"], "billing server")
        self.assertEqual(case.context["speaker"], "Lan")
        self.assertEqual(case.context["quotes"][1], {"turn_id": "T03", "quote": "It was the disk."})
        # Null scope keys are not stored, so they stay unscoped.
        self.assertNotIn("condition", case.context)

        self.assertEqual(rule.supported_by, [0])
        self.assertEqual(procedure.context["steps"][0], "Stop the scheduler.")
        self.assertTrue(procedure.claim.endswith("3. Start the job."))
        self.assertEqual(procedure.supported_by, [])

    def test_rejects_malformed_items(self) -> None:
        cases = {
            "procedure without steps": [_item(type="procedure")],
            "steps on a fact": [_item(steps=["a", "b"])],
            "no quotes": [_item(quotes=[])],
            "unknown certainty": [_item(certainty="confirmed")],
            "based_on a non-case": [_item(key="fact-a"), _item(based_on=["fact-a"])],
            "based_on itself": [_item(type="case"), _item(key="tip", based_on=["tip"])],
            "based_on an unknown key": [_item(type="case", key="case-a"), _item(based_on=["case-b"])],
            "based_on a position": [_item(type="case"), _item(based_on=[0])],
            "case based_on a case": [
                _item(type="case", key="case-a"), _item(type="case", based_on=["case-a"]),
            ],
            "duplicate keys": [_item(key="same"), _item(key="same")],
            "no key": [{k: v for k, v in _item().items() if k != "key"}],
            "unknown field": [_item(confidence=0.9)],
        }
        for name, items in cases.items():
            with self.subTest(name), self.assertRaises(KnowledgeExtractionError):
                _extract({"items": items})

    def test_based_on_keys_become_positions_in_any_order(self) -> None:
        lesson, case = _extract({"items": [
            _item(type="heuristic", based_on=["case-later"]), _item(type="case", key="case-later"),
        ]}).knowledge_items
        self.assertEqual(lesson.supported_by, [1])
        self.assertEqual(case.supported_by, [])

    def test_asks_again_with_the_validation_error_then_gives_up(self) -> None:
        bad = {"items": [_item(based_on=["case-missing"])]}
        client = FakeLLM(bad, EXTRACTION)
        self.assertEqual(len(_extract(bad, client=client, validation_retries=1).knowledge_items), 3)
        self.assertNotIn("Rejected response", client.prompts[0])
        self.assertIn("unknown key 'case-missing'", client.prompts[1])
        self.assertTrue(client.prompts[1].startswith(client.prompts[0]))

        client = FakeLLM(bad)
        with self.assertRaises(KnowledgeExtractionError):
            _extract(bad, client=client, validation_retries=1)
        self.assertEqual(len(client.prompts), 2)
        with self.assertRaises(KnowledgeExtractionError):
            _extract(bad)

    def test_prompt_asks_for_the_speakers_language(self) -> None:
        self.assertIn("language the speakers use", GENERAL.extract_prompt)
        self.assertEqual(GENERAL.extraction_version, "general-items-v2")

    def test_empty_conversation_result_is_valid(self) -> None:
        self.assertEqual(_extract({"items": []}).knowledge_items, [])


class GeneralRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        items = _extract(EXTRACTION).knowledge_items
        self.records = [
            KnowledgeRecord.from_extracted(item, evidence_id=uuid4()) for item in items
        ]

    def test_quotes_and_steps_are_not_indexed(self) -> None:
        text = self.records[2].embedding_text(GENERAL.embedding_context_keys)
        self.assertNotIn("quotes=", text)
        self.assertNotIn("steps=", text)
        self.assertIn("certainty=stated", text)
        case_text = self.records[0].embedding_text(GENERAL.embedding_context_keys)
        self.assertIn("Context: applies_to=billing server; certainty=stated", case_text)

    def test_search_payload_omits_quotes_but_get_knowledge_keeps_them(self) -> None:
        record = self.records[0]
        hit = KnowledgeSearchHit(record=record, hybrid_score=0.5, confident=True)
        self.assertNotIn("quotes", _record_payload(record, date(2026, 10, 15), search_hit=hit)["context"])
        self.assertIn("quotes", _record_payload(record, date(2026, 10, 15))["context"])

    def test_verifier_checks_quotes_against_their_turns(self) -> None:
        record = self.records[0]
        record.context["quotes"].append({"turn_id": "T02", "quote": "It was the disk."})
        record.context["quotes"].append({"quote": "the backup ran twice"})
        (result,) = asyncio.run(KnowledgeVerifier().averify(TRANSCRIPT, [record]))
        self.assertEqual(result["issues"], [
            "evidence 3: quote is not in turn T02 (it is elsewhere in the transcript)",
            "evidence 4: quote is not in the transcript",
        ])
        self.assertFalse(result["passed"])

    def test_verifier_passes_faithful_records(self) -> None:
        results = asyncio.run(KnowledgeVerifier().averify(TRANSCRIPT, self.records))
        self.assertEqual([result["issues"] for result in results], [[], [], []])


if __name__ == "__main__":
    unittest.main()
