"""Unit tests for the knowledge retrieval agent (fake model, fake store)."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from backend.src.agents.agent.service import (
    InvalidKnowledgeCitation,
    KnowledgeAgentService,
    KnowledgeAgentUnavailable,
    citations_from_evidence,
)
from backend.src.agents.agent.state import merge_evidence
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage import KnowledgeRecord, RawConversation, StorageError


EVIDENCE_ID = uuid4()
RECORD = KnowledgeRecord(
    subject="rear photoelectric sensor",
    claim="Clean the lens before replacing the sensor.",
    context={"machine_id": "M01"},
    type=KnowledgeType.PROCEDURE,
    evidence_id=EVIDENCE_ID,
    stated_by="Maintenance Expert",
    valid_from=date(2026, 1, 1),
)


class ScriptedChatModel(BaseChatModel):
    """Returns queued AI messages in order and records what it was shown."""

    responses: list[AIMessage]
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._calls

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._calls.append(list(messages))
        message = self.responses[len(self._calls) - 1]
        return ChatResult(generations=[ChatGeneration(message=message)])


class FakeStore:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.searches: list[tuple[str, Any, int]] = []

    async def search_knowledge(self, query, *, filters=None, k=5):
        if self.fail:
            raise StorageError("connection refused")
        self.searches.append((query, filters, k))
        return [(RECORD, 0.12)]

    async def get_knowledge(self, ids):
        return [RECORD] if RECORD.id in ids else []

    async def get_conversation(self, conversation_id):
        if conversation_id != EVIDENCE_ID:
            return None
        return RawConversation(
            id=EVIDENCE_ID,
            transcript="Expert: clean the lens before replacing the sensor.",
            stated_by="Maintenance Expert",
            created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )


def _search_call(**args: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "search_knowledge", "args": args, "id": "call_1"}],
    )


def _service(store: FakeStore, *responses: AIMessage) -> tuple[KnowledgeAgentService, ScriptedChatModel]:
    model = ScriptedChatModel(responses=list(responses))
    return KnowledgeAgentService(store=store, model=model, timeout_seconds=10), model


class CitationTest(unittest.TestCase):
    def evidence(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": "knowledge",
                "knowledge_id": str(RECORD.id),
                "subject": RECORD.subject,
                "claim": RECORD.claim,
                "type": "procedure",
                "evidence_id": str(EVIDENCE_ID),
                "stated_by": "Maintenance Expert",
            }
        ]

    def test_resolves_citations_once_in_order(self) -> None:
        answer = f"Clean the lens [K:{RECORD.id}]. Really [K:{str(RECORD.id).upper()}]."
        (citation,) = citations_from_evidence(answer, self.evidence())
        self.assertEqual(citation.knowledge_id, str(RECORD.id))
        self.assertEqual(citation.evidence_id, str(EVIDENCE_ID))
        self.assertEqual(citation.type, "procedure")

    def test_unknown_citation_is_rejected(self) -> None:
        with self.assertRaises(InvalidKnowledgeCitation):
            citations_from_evidence(f"Nope [K:{uuid4()}]", self.evidence())

    def test_answer_without_markers_has_no_citations(self) -> None:
        self.assertEqual(citations_from_evidence("No evidence found.", self.evidence()), ())


class MergeEvidenceTest(unittest.TestCase):
    def test_deduplicates_and_enriches(self) -> None:
        merged = merge_evidence(
            [{"kind": "knowledge", "knowledge_id": "a", "claim": "x", "stated_by": None}],
            [
                {"kind": "knowledge", "knowledge_id": "a", "claim": "", "stated_by": "E"},
                {"kind": "conversation", "evidence_id": "a"},
            ],
        )
        self.assertEqual(
            merged,
            [
                {"kind": "knowledge", "knowledge_id": "a", "claim": "x", "stated_by": "E"},
                {"kind": "conversation", "evidence_id": "a"},
            ],
        )


class KnowledgeAgentServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_answers_with_validated_citation(self) -> None:
        store = FakeStore()
        service, model = _service(
            store,
            _search_call(query="unstable sensor signal", machine_id="M01"),
            AIMessage(content=f"Clean the lens before replacing it [K:{RECORD.id}]."),
        )

        answer = await service.ask("The rear sensor on M01 is unstable, what should I do?")

        self.assertEqual(answer.answer, f"Clean the lens before replacing it [K:{RECORD.id}].")
        (citation,) = answer.citations
        self.assertEqual(UUID(citation.knowledge_id), RECORD.id)
        self.assertEqual(citation.stated_by, "Maintenance Expert")

        query, filters, k = store.searches[0]
        self.assertEqual(query, "unstable sensor signal")
        self.assertEqual(filters["$or"][0], {"context.machine_id": {"$eq": "M01"}})
        self.assertEqual(k, 5)

        tool_message = model.calls[1][-1]
        self.assertIsInstance(tool_message, ToolMessage)
        self.assertIn('"validity": "current"', tool_message.content)
        self.assertNotIn("embedding", tool_message.content)
        self.assertIn("[Turn context: current UTC time is", model.calls[0][-1].content)

    async def test_stream_emits_status_and_citations(self) -> None:
        service, _ = _service(
            FakeStore(),
            _search_call(query="sensor"),
            AIMessage(content=f"Clean it [K:{RECORD.id}]."),
        )
        session = service.start_stream("sensor?", thread_id="t1")
        events = [event async for event in session.events]
        names = [event.event for event in events]
        self.assertEqual(names[0], "start")
        self.assertIn({"phase": "searching", "tool": "search_knowledge"}, [e.data for e in events])
        self.assertEqual(names[-2:], ["citations", "done"])
        text = "".join(e.data["text"] for e in events if e.event == "delta")
        self.assertEqual(text, f"Clean it [K:{RECORD.id}].")

    async def test_invented_citation_is_rejected(self) -> None:
        service, _ = _service(
            FakeStore(),
            _search_call(query="sensor"),
            AIMessage(content=f"Replace it [K:{uuid4()}]."),
        )
        with self.assertRaises(InvalidKnowledgeCitation):
            await service.ask("sensor?")

    async def test_storage_failure_is_reported_as_unavailable(self) -> None:
        service, _ = _service(
            FakeStore(fail=True),
            _search_call(query="sensor"),
            AIMessage(content="unused"),
        )
        with self.assertRaises(KnowledgeAgentUnavailable) as caught:
            await service.ask("sensor?")
        self.assertEqual(caught.exception.error_type, "StorageError")

    async def test_evidence_resets_between_turns(self) -> None:
        service, _ = _service(
            FakeStore(),
            _search_call(query="sensor"),
            AIMessage(content=f"Clean it [K:{RECORD.id}]."),
            # Second turn: no retrieval, so the earlier citation is no longer valid.
            AIMessage(content=f"As I said [K:{RECORD.id}]."),
        )
        first = [e async for e in service.start_stream("sensor?", thread_id="t").events]
        self.assertEqual(first[-1].event, "done")
        with self.assertRaises(InvalidKnowledgeCitation):
            [e async for e in service.start_stream("again?", thread_id="t").events]


if __name__ == "__main__":
    unittest.main()
