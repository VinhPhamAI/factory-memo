"""Industrial QA application service using the shared retrieval and answer pipeline."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, replace
import logging
import re
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from backend.knowledge_bases.stores import open_document_store
from backend.knowledge_bases.industrial_qa.config import load_config
from backend.knowledge_bases.industrial_qa.pipeline import IndustrialQaPipeline
from backend.knowledge_bases.industrial_qa.scope import inherit_follow_up_scope
from backend.knowledge_bases.industrial_qa.retrieval import DOCUMENT_QUERY_INSTRUCTION
from backend.src.agents.agent.config import RUN_TIMEOUT_SECONDS
from backend.src.agents.agent.service import (
    CITATION_RE, DOCUMENT_CITATION_RE,
    AgentStreamSession, InvalidKnowledgeCitation, KnowledgeAgentError,
    KnowledgeAgentService, KnowledgeAgentUnavailable, KnowledgeAgentUpstreamError,
    _StreamEventFactory, citations_from_evidence,
)
from backend.src.services.knowledge_conflicts import conflict_notes
from backend.src.storage import StorageError
from backend.src.storage.knowledge_conflicts import ConflictStore


logger = logging.getLogger(__name__)
MAX_CONFLICT_REASON = 300


@dataclass
class _Thread:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    history: list[tuple[str, str]] = field(default_factory=list)


def _rewrite_summary(plan):
    """Public trace of query rewriting: only why and whether, never the rewritten text."""
    return {"reason": plan.reason, "status": plan.status} if plan is not None else None


def answer_with_citations(written, retrieval):
    """Resolve ephemeral labels to actual records/chunks before exposing text."""
    sources = {source.label: source for source in retrieval.sources}
    evidence, markers = [], []
    for label in dict.fromkeys(written.sources):
        if label not in sources:
            raise InvalidKnowledgeCitation("Answer cites an unknown source label")
        source = sources[label]
        if source.record is not None:
            record = source.record
            evidence.append({"kind": "knowledge", "knowledge_id": str(record.id),
                "subject": record.subject, "claim": record.claim, "type": record.type.value,
                "evidence_id": str(record.evidence_id) if record.evidence_id else None,
                "stated_by": record.stated_by, "context": record.context,
                "status": record.status.value})
            markers.append(f"[K:{record.id}]")
        else:
            passage = source.passage
            evidence.append({"kind": "document", "doc_id": passage.doc_id, "title": passage.title})
            markers.append(f"[D:{passage.doc_id}]")
    # Writer returns prose and structured labels; validate any full IDs it
    # happens to put in prose as well, then normalize short label markers.
    for marker in re.findall(r"\[(?:K|D):[^\]]*\]", written.answer):
        if not (CITATION_RE.fullmatch(marker) or DOCUMENT_CITATION_RE.fullmatch(marker)):
            raise InvalidKnowledgeCitation("Answer contains a malformed citation")
    citations_from_evidence(written.answer, evidence)
    def replace_label(match):
        label = match[1]
        if label not in written.sources:
            raise InvalidKnowledgeCitation("Answer cites a label missing from its sources")
        source = sources[label]
        return f"[K:{source.record.id}]" if source.record else f"[D:{source.passage.doc_id}]"
    answer = re.sub(r"\[([KD]\d+)\]", replace_label, written.answer).strip()
    if markers:
        answer += "\n\n" + " ".join(marker for marker in dict.fromkeys(markers) if marker not in answer)
    return answer.strip(), citations_from_evidence(answer, evidence)


async def with_conflicts(citations, retrieval, store, conflicts):
    """Knowledge citations gain the searchable records they conflict with.

    Presentation only: the answer text and its policy are unchanged, and a
    failure to read conflicts leaves the citations as they are.
    """
    records = {str(source.record.id): source.record for source in retrieval.sources if source.record}
    cited = [records[c.knowledge_id] for c in citations if getattr(c, "knowledge_id", None) in records]
    if not cited:
        return citations
    try:
        notes = await conflict_notes(store, conflicts, cited)
    except Exception:  # noqa: BLE001 - a warning must never break an answer
        logger.warning("Could not read the conflicts of cited records", exc_info=True)
        return citations
    def noted(citation):
        found = notes.get(UUID(citation.knowledge_id)) if getattr(citation, "knowledge_id", None) else None
        if not found:
            return citation
        return replace(citation, conflicts=[
            {**note, "reason": (note["reason"] or "")[:MAX_CONFLICT_REASON] or None} for note in found])
    return tuple(noted(citation) for citation in citations)


class IndustrialQaService(KnowledgeAgentService):
    """Same HTTP/CLI interface, with KB-specific retrieval and answer policy."""
    def __init__(self, spec, store, *, documents=None, pipeline=None,
                 timeout_seconds=RUN_TIMEOUT_SECONDS, conflicts=None):
        self._spec, self._store = spec, store
        # Read-only: the conflict stage creates the table; without it only record links are used.
        if conflicts is None and getattr(store, "engine", None) is not None:
            conflicts = ConflictStore(store.engine, schema=getattr(store, "schema", None) or "public")
        self._conflicts = conflicts
        self._documents, self._owns_documents = documents, documents is None
        self._document_lock = asyncio.Lock()
        self.pipeline = pipeline if pipeline is not None else IndustrialQaPipeline()
        self._conversation = (getattr(self.pipeline, "config", None) or load_config()).conversation
        self._timeout_seconds = timeout_seconds
        self._threads: dict[str, _Thread] = {}

    async def _document_store(self):
        async with self._document_lock:
            if self._documents is None:
                self._documents = await open_document_store(self._spec,
                    query_instruction=DOCUMENT_QUERY_INSTRUCTION, init_schema=False)
        return self._documents

    def start_stream(self, query: str, *, thread_id: str):
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        request_id = uuid4().hex
        return AgentStreamSession(request_id, thread_id,
            self._stream(query.strip(), thread_id=thread_id, request_id=request_id))

    async def _stream(self, query, *, thread_id, request_id):
        events = _StreamEventFactory(request_id=request_id, thread_id=thread_id)
        started = monotonic()
        yield events.create("run.started", {"query_chars": len(query)})
        thread = self._threads.setdefault(thread_id, _Thread())
        try:
            async with asyncio.timeout(self._timeout_seconds), thread.lock:
                documents = await self._document_store()
                # A follow-up without scope of its own inherits the latest scoped question.
                retrieval_query = inherit_follow_up_scope(query, thread.history)
                call_id = f"{request_id}:retrieval"
                yield events.create("tool.started", {"tool_call_id": call_id,
                    "tool": "industrial_qa_retrieval", "phase": "retrieving",
                    "input": {"query": retrieval_query[:2000]}})
                retrieved = await self.pipeline.retrieve(query, self._store, documents, history=thread.history)
                yield events.create("tool.completed", {"tool_call_id": call_id,
                    "tool": "industrial_qa_retrieval", "status": "success",
                    "duration_ms": int((monotonic() - started) * 1000),
                    "output": {"count": len(retrieved.sources), "candidate_count": retrieved.candidate_count,
                        "assets": list(retrieved.assets), "family": retrieved.family,
                        "knowledge_ids": [str(s.record.id) for s in retrieved.sources if s.record],
                        "document_ids": [s.passage.doc_id for s in retrieved.sources if s.passage],
                        "query_rewrite": _rewrite_summary(retrieved.query_plan)}})
                limit = self._conversation.history_characters
                history = "\n\n".join(f"User: {q}\nAssistant: {a}" for q, a in thread.history)[-limit:] if limit else ""
                written, _ = await self.pipeline.write(query, retrieved, history=history)
                answer, citations = answer_with_citations(written, retrieved)
                citations = await with_conflicts(citations, retrieved, self._store, self._conflicts)
                # Labels (K1, D2) only mean something within their own turn.
                history_answer = re.sub(r"\s*\[[KD]\d+\]", "", written.answer)
                turns = self._conversation.history_turns
                thread.history = [*thread.history, (query, history_answer)][-turns:] if turns else []
                yield events.create("answer.delta", {"text": answer})
                yield events.create("answer.citations", {"citations": [asdict(c) for c in citations]})
                yield events.create("run.completed", {"duration_ms": int((monotonic()-started)*1000),
                    "citation_count": len(citations)})
        except KnowledgeAgentError as exc:
            exc.request_id = request_id
            raise
        except (StorageError, SQLAlchemyError) as exc:
            raise KnowledgeAgentUnavailable("Industrial QA stores are unavailable",
                request_id=request_id, error_type=type(exc).__name__, reason=str(exc)) from exc
        except Exception as exc:
            raise KnowledgeAgentUpstreamError("Industrial QA pipeline failed",
                request_id=request_id, error_type=type(exc).__name__, reason=str(exc)) from exc

    async def delete_thread(self, thread_id):
        thread = self._threads.get(thread_id)
        if thread:
            async with thread.lock:
                thread.history.clear()
                self._threads.pop(thread_id, None)

    async def aclose(self):
        if self._owns_documents and self._documents is not None:
            await self._documents.aclose()
            self._documents = None
        self._threads.clear()
