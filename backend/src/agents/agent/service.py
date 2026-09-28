"""Application service that runs the knowledge agent and validates citations."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any
from uuid import uuid4

from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Overwrite
from openai import APIError
from sqlalchemy.exc import SQLAlchemyError

from backend.src.agents.agent.config import (
    RECURSION_LIMIT,
    RUN_TIMEOUT_SECONDS,
    build_chat_model_from_env,
)
from backend.src.agents.agent.graph import build_graph
from backend.src.agents.agent.state import Evidence, KnowledgeAgentContext
from backend.src.storage import KnowledgeStore, StorageError


logger = logging.getLogger(__name__)
CITATION_RE = re.compile(
    r"\[K:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]"
)
LOG_CONTENT_LIMIT = 2_000
SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|authorization)(\s*[:=]\s*)([^\s,;]+)"
)
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
DATABASE_URL_RE = re.compile(
    r"(?i)(postgres(?:ql)?(?:\+\w+)?://)([^\s/@:]+):([^\s/@]+)@"
)


def _safe_text(value: object, *, limit: int = LOG_CONTENT_LIMIT) -> str:
    text = SECRET_VALUE_RE.sub(r"\1\2<redacted>", str(value))
    text = OPENAI_KEY_RE.sub("<redacted>", text)
    text = DATABASE_URL_RE.sub(r"\1<redacted>:<redacted>@", text)
    if len(text) > limit:
        return f"{text[:limit]}... <truncated {len(text) - limit} chars>"
    return text


class KnowledgeAgentError(RuntimeError):
    """Base error carrying a safe diagnostic for API responses."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        error_type: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.error_type = error_type or type(self).__name__
        self.reason = _safe_text(reason or message, limit=1_000)

    def public_detail(self) -> dict[str, str]:
        detail = {
            "message": str(self),
            "error_type": self.error_type,
            "reason": self.reason,
        }
        if self.request_id:
            detail["request_id"] = self.request_id
        return detail


class KnowledgeAgentUnavailable(KnowledgeAgentError):
    """The knowledge base or embedding service cannot currently serve requests."""


class KnowledgeAgentUpstreamError(KnowledgeAgentError):
    """The model invocation failed, timed out, or produced no answer."""


class InvalidKnowledgeCitation(KnowledgeAgentError):
    """The model cited a knowledge id that no tool returned."""


@dataclass(frozen=True)
class KnowledgeCitation:
    knowledge_id: str
    subject: str | None
    claim: str | None
    type: str | None
    evidence_id: str | None
    stated_by: str | None


@dataclass(frozen=True)
class KnowledgeAnswer:
    answer: str
    citations: tuple[KnowledgeCitation, ...]
    request_id: str = ""


@dataclass(frozen=True)
class AgentStreamEvent:
    event: str
    data: dict[str, Any]


@dataclass(frozen=True)
class AgentStreamSession:
    request_id: str
    thread_id: str
    events: AsyncIterator[AgentStreamEvent]


def _content_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
            if isinstance(block, str)
            or (isinstance(block, dict) and isinstance(block.get("text"), str))
        )
    return str(content)


def _root_exception(exc: BaseException) -> BaseException:
    current = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        nested = getattr(current, "exceptions", None)
        if nested:
            current = nested[0]
            continue
        cause = current.__cause__ or current.__context__
        if cause is None:
            break
        current = cause
    return current


def _contains_exception(
    exc: BaseException, classes: tuple[type[BaseException], ...]
) -> bool:
    if isinstance(exc, classes):
        return True
    nested = getattr(exc, "exceptions", None) or ()
    if any(_contains_exception(item, classes) for item in nested):
        return True
    cause = exc.__cause__ or exc.__context__
    return cause is not None and _contains_exception(cause, classes)


def _diagnostic(exc: BaseException) -> tuple[str, str]:
    root = _root_exception(exc)
    reason = _safe_text(root, limit=1_000) or "No error detail was supplied by the dependency"
    return type(root).__name__, reason


def _sanitize_tool_payload(value: Any, *, key: str | None = None) -> Any:
    if key == "transcript" and isinstance(value, str):
        return f"<omitted {len(value)} chars>"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_tool_payload(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_tool_payload(item) for item in value]
    return value


def _logged_message_content(message: Any) -> str:
    text = _content_text(message).strip()
    if isinstance(message, ToolMessage):
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return _safe_text(text)
        text = json.dumps(_sanitize_tool_payload(payload), ensure_ascii=False, default=str)
    return _safe_text(text)


def _log_new_messages(request_id: str, messages: list[Any], already_logged: int) -> int:
    for index, message in enumerate(messages[already_logged:], start=already_logged):
        tool_calls = getattr(message, "tool_calls", None) or []
        logger.info(
            "Knowledge agent message request_id=%s index=%d role=%s tool_call_id=%s "
            "tool_calls=%s content=%s",
            request_id,
            index,
            getattr(message, "type", type(message).__name__),
            getattr(message, "tool_call_id", None),
            _safe_text(json.dumps(tool_calls, ensure_ascii=False, default=str)),
            _logged_message_content(message),
        )
    return len(messages)


def _tool_status(tool_name: str) -> str:
    return "searching" if tool_name.startswith("search_") else "reading"


def citations_from_evidence(
    answer: str, evidence: list[Evidence]
) -> tuple[KnowledgeCitation, ...]:
    """Resolve [K:<id>] markers against knowledge the tools actually returned."""
    records = {
        str(item["knowledge_id"]).lower(): item
        for item in evidence
        if item.get("kind") == "knowledge" and item.get("knowledge_id")
    }
    ordered_ids: list[str] = []
    for raw_id in CITATION_RE.findall(answer):
        knowledge_id = raw_id.lower()
        if knowledge_id not in records:
            raise InvalidKnowledgeCitation(
                f"Citation K:{knowledge_id} is not present in retrieved evidence"
            )
        if knowledge_id not in ordered_ids:
            ordered_ids.append(knowledge_id)
    return tuple(
        KnowledgeCitation(
            knowledge_id=knowledge_id,
            subject=records[knowledge_id].get("subject"),
            claim=records[knowledge_id].get("claim"),
            type=records[knowledge_id].get("type"),
            evidence_id=records[knowledge_id].get("evidence_id"),
            stated_by=records[knowledge_id].get("stated_by"),
        )
        for knowledge_id in ordered_ids
    )


class KnowledgeAgentService:
    """Runs the retrieval agent for one question or a multi-turn thread."""

    def __init__(
        self,
        *,
        store: KnowledgeStore,
        model: Any,
        timeout_seconds: float = RUN_TIMEOUT_SECONDS,
        checkpointer: InMemorySaver | None = None,
    ) -> None:
        self._store = store
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._checkpointer = checkpointer or InMemorySaver()
        self._graph = build_graph(model, checkpointer=self._checkpointer)

    @classmethod
    def from_env(cls, store: KnowledgeStore | None = None) -> "KnowledgeAgentService":
        return cls(
            store=store or KnowledgeStore.from_env(),
            model=build_chat_model_from_env(),
        )

    def start_stream(self, query: str, *, thread_id: str) -> AgentStreamSession:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        request_id = uuid4().hex
        return AgentStreamSession(
            request_id=request_id,
            thread_id=thread_id,
            events=self._stream_graph(
                query=query.strip(), thread_id=thread_id, request_id=request_id
            ),
        )

    async def _stream_graph(
        self,
        *,
        query: str,
        thread_id: str,
        request_id: str,
    ) -> AsyncIterator[AgentStreamEvent]:
        graph = self._graph
        started = monotonic()
        now = datetime.now(timezone.utc)
        context = KnowledgeAgentContext(store=self._store, now=now, request_id=request_id)
        config = {
            "recursion_limit": RECURSION_LIMIT,
            "configurable": {"thread_id": thread_id},
        }
        snapshot = await graph.aget_state(config)
        baseline_messages = len((snapshot.values or {}).get("messages") or [])
        logged_messages = baseline_messages
        observed_messages = baseline_messages
        emitted_tool_calls: set[str] = set()
        current_generation: str | None = None
        current_generation_uses_tools = False
        visible_answer = ""
        result: dict[str, Any] | None = None

        logger.info(
            "Knowledge agent started request_id=%s thread_id=%s query_chars=%d "
            "history_messages=%d",
            request_id,
            thread_id,
            len(query),
            baseline_messages,
        )
        yield AgentStreamEvent("start", {"request_id": request_id, "thread_id": thread_id})

        turn_message = f"[Turn context: current UTC time is {now.isoformat()}]\n\n{query}"
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async for part in graph.astream(
                    {
                        "messages": [HumanMessage(content=turn_message)],
                        "evidence": Overwrite([]),
                    },
                    config=config,
                    context=context,
                    stream_mode=["messages", "values"],
                    version="v2",
                ):
                    part_type = part.get("type")
                    if part_type == "messages":
                        message, metadata = part["data"]
                        if metadata.get("langgraph_node") != "model":
                            continue
                        generation = str(getattr(message, "id", None) or "model")
                        if generation != current_generation:
                            current_generation = generation
                            current_generation_uses_tools = False
                        calls = [
                            *(getattr(message, "tool_calls", None) or []),
                            *(getattr(message, "tool_call_chunks", None) or []),
                        ]
                        if calls:
                            # Text streamed before a tool call was not the answer.
                            if not current_generation_uses_tools and visible_answer:
                                visible_answer = ""
                                yield AgentStreamEvent("reset", {})
                            current_generation_uses_tools = True
                            for call in calls:
                                tool_name = str(call.get("name") or "")
                                call_id = str(call.get("id") or f"{generation}:{tool_name}")
                                if tool_name and call_id not in emitted_tool_calls:
                                    emitted_tool_calls.add(call_id)
                                    yield AgentStreamEvent(
                                        "status",
                                        {"phase": _tool_status(tool_name), "tool": tool_name},
                                    )
                            continue
                        if current_generation_uses_tools:
                            continue
                        delta = _content_text(message)
                        if delta:
                            visible_answer += delta
                            yield AgentStreamEvent("delta", {"text": delta})
                        continue

                    if part_type != "values":
                        continue
                    result = part["data"]
                    messages = list(result.get("messages") or [])
                    logged_messages = _log_new_messages(request_id, messages, logged_messages)
                    if any(
                        isinstance(message, ToolMessage)
                        for message in messages[observed_messages:]
                    ):
                        yield AgentStreamEvent("status", {"phase": "synthesizing"})
                    observed_messages = len(messages)
        except TimeoutError as exc:
            reason = f"Agent exceeded the {self._timeout_seconds:g}s run timeout"
            logger.warning(
                "Knowledge agent timed out request_id=%s thread_id=%s", request_id, thread_id
            )
            raise KnowledgeAgentUpstreamError(
                "Knowledge agent request timed out",
                request_id=request_id,
                error_type="TimeoutError",
                reason=reason,
            ) from exc
        except asyncio.CancelledError:
            logger.info(
                "Knowledge agent cancelled request_id=%s thread_id=%s", request_id, thread_id
            )
            raise
        except Exception as exc:
            error_type, reason = _diagnostic(exc)
            retrieval_failed = _contains_exception(exc, (StorageError, SQLAlchemyError))
            logger.exception(
                "Knowledge agent failed request_id=%s thread_id=%s phase=%s "
                "error_type=%s reason=%s",
                request_id,
                thread_id,
                "retrieval" if retrieval_failed else "graph",
                error_type,
                reason,
            )
            if retrieval_failed:
                raise KnowledgeAgentUnavailable(
                    "Knowledge base is unavailable",
                    request_id=request_id,
                    error_type=error_type,
                    reason=reason,
                ) from exc
            message = (
                "Model or embedding request failed"
                if _contains_exception(exc, (APIError,))
                else "Knowledge agent run failed"
            )
            raise KnowledgeAgentUpstreamError(
                message, request_id=request_id, error_type=error_type, reason=reason
            ) from exc

        if result is None:
            raise KnowledgeAgentUpstreamError(
                "Knowledge agent returned no result",
                request_id=request_id,
                error_type="EmptyGraphResult",
            )

        messages = list(result.get("messages") or [])
        final = next(
            (
                message
                for message in reversed(messages[baseline_messages:])
                if isinstance(message, AIMessage) and not message.tool_calls
            ),
            None,
        )
        answer = _content_text(final).strip() if final is not None else ""
        if not answer:
            raise KnowledgeAgentUpstreamError(
                "Knowledge agent returned no final answer",
                request_id=request_id,
                error_type="MissingFinalAnswer",
            )

        evidence = list(result.get("evidence") or [])
        try:
            citations = citations_from_evidence(answer, evidence)
        except InvalidKnowledgeCitation as exc:
            logger.warning(
                "Knowledge agent invalid citation request_id=%s thread_id=%s reason=%s",
                request_id,
                thread_id,
                exc,
            )
            raise InvalidKnowledgeCitation(
                "Knowledge agent returned an invalid citation",
                request_id=request_id,
                reason=str(exc),
            ) from exc

        if visible_answer != answer:
            if visible_answer:
                yield AgentStreamEvent("reset", {})
            yield AgentStreamEvent("delta", {"text": answer})
        yield AgentStreamEvent("citations", {"citations": citations})
        yield AgentStreamEvent("done", {"request_id": request_id, "thread_id": thread_id})

        current_messages = messages[baseline_messages:]
        logger.info(
            "Knowledge agent completed request_id=%s thread_id=%s duration_ms=%d "
            "model_calls=%d tool_calls=%d evidence=%d citations=%d",
            request_id,
            thread_id,
            int((monotonic() - started) * 1000),
            sum(isinstance(message, AIMessage) for message in current_messages),
            sum(
                len(message.tool_calls)
                for message in current_messages
                if isinstance(message, AIMessage)
            ),
            len(evidence),
            len(citations),
        )

    async def ask(self, query: str) -> KnowledgeAnswer:
        """Answer one standalone question; the thread is discarded afterwards."""
        thread_id = uuid4().hex
        session = self.start_stream(query, thread_id=thread_id)
        answer = ""
        citations: tuple[KnowledgeCitation, ...] = ()
        completed = False
        try:
            async for event in session.events:
                if event.event == "delta":
                    answer += str(event.data.get("text") or "")
                elif event.event == "reset":
                    answer = ""
                elif event.event == "citations":
                    citations = tuple(event.data.get("citations") or ())
                elif event.event == "done":
                    completed = True
        finally:
            await self.delete_thread(thread_id)
        if not completed:
            raise KnowledgeAgentUpstreamError(
                "Knowledge agent stream ended before completion",
                request_id=session.request_id,
                error_type="IncompleteStream",
            )
        return KnowledgeAnswer(
            answer=answer, citations=citations, request_id=session.request_id
        )

    async def delete_thread(self, thread_id: str) -> None:
        await self._checkpointer.adelete_thread(thread_id)
