"""Bound active requests and translate graph progress into public events."""

import asyncio
from contextlib import aclosing
from typing import Awaitable, Callable
from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage

from denso_agent.api.sse import encode_event
from denso_agent.config import RuntimeSettings
from denso_agent.runtime.errors import AgentError
from denso_agent.schemas.events import CompleteEvent, ErrorEvent, StatusEvent, TextEvent


def _answer_from_update(update: object) -> str:
    """Extract the single completed assistant answer from a graph update.

    Args:
        update: LangGraph update containing the current answer node's result.

    Returns:
        The original answer text, preserving whitespace and Unicode characters.

    Raises:
        ValueError: The update lacks one unambiguous, nonempty assistant string.
    """
    if not isinstance(update, dict) or set(update) != {"answer"}:
        raise ValueError("Expected one answer-node result.")
    node = update["answer"]
    messages = node.get("messages") if isinstance(node, dict) else None
    if (
        not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], AIMessage)
        or not isinstance(messages[0].content, str)
        or not messages[0].content.strip()
    ):
        raise ValueError("Expected one completed assistant text message.")
    return messages[0].content


class Admission:
    """Track only active conversations within one event-loop process."""

    def __init__(self, capacity: int):
        """Set the maximum simultaneous conversations.

        Args:
            capacity: Positive request limit validated by runtime settings.
        """
        self._capacity = capacity
        self._active: dict[str, object] = {}

    @property
    def active_count(self) -> int:
        """Return the number of currently admitted conversations."""
        return len(self._active)

    def acquire(self, conversation_id: str) -> None:
        """Admit without awaiting so conflict and capacity checks are atomic.

        Args:
            conversation_id: Canonical conversation UUID string.
        Raises:
            HTTPException: This conversation is busy or capacity is exhausted.
        """
        if conversation_id in self._active:
            raise HTTPException(
                409,
                detail={
                    "code": "conversation_busy",
                    "message": "Conversation already has an active request.",
                },
            )
        if len(self._active) >= self._capacity:
            raise HTTPException(
                429,
                detail={
                    "code": "capacity_exceeded",
                    "message": "Agent request capacity is exhausted.",
                },
            )
        self._active[conversation_id] = object()

    def reservation(self, conversation_id: str) -> object:
        """Identify the current reservation for cleanup after transport completion.

        Args:
            conversation_id: Conversation already admitted by this process.
        Returns:
            An opaque identity that distinguishes this request from a later one.
        Raises:
            KeyError: The conversation has no active reservation.
        """
        return self._active[conversation_id]

    def release(self, conversation_id: str, reservation: object | None = None) -> None:
        """Remove a finished conversation without retaining an inactive lock.

        Args:
            conversation_id: Conversation admitted by this process.
            reservation: Optional reservation identity; stale cleanup cannot remove
                a newer request when this identity is supplied.
        """
        if reservation is None or self._active.get(conversation_id) is reservation:
            self._active.pop(conversation_id, None)


class AgentRuntime:
    """Own admission and graph execution while application lifespan owns clients."""

    def __init__(
        self, graph, ready: Callable[[], Awaitable[bool]], settings: RuntimeSettings
    ):
        """Connect the compiled graph, bounded readiness probe and request limits.

        Args:
            graph: Compiled LangGraph exposing asynchronous custom streaming.
            ready: Database readiness probe that makes no model call.
            settings: Validated runtime limits and credentials.
        """
        self.graph = graph
        self.ready = ready
        self.settings = settings
        self.admission = Admission(settings.max_concurrent_requests)

    async def stream(self, conversation_id: UUID, message: str, request_id: UUID):
        """Stream provisional text, then return the full answer after persistence.

        Args:
            conversation_id: Caller-owned checkpoint thread identifier.
            message: Validated current human input.
            request_id: Server-generated tracing identifier.
        Yields:
            Typed SSE frames; at most one connected terminal event.
        """
        sequence = 0
        final_answer: str | None = None

        def event(name, event_type, **fields):
            """Number and encode the next public event in this request."""
            nonlocal sequence
            sequence += 1
            return encode_event(
                name,
                event_type(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    sequence=sequence,
                    **fields,
                ),
            )

        try:
            yield event("status", StatusEvent)
            async with asyncio.timeout(self.settings.request_timeout):
                iterator = self.graph.astream(
                    {"messages": [HumanMessage(content=message)]},
                    config={"configurable": {"thread_id": str(conversation_id)}},
                    stream_mode=["custom", "updates"],
                    version="v2",
                    durability="sync",
                )
                async with aclosing(iterator):
                    async for part in iterator:
                        if part["type"] == "custom":
                            delta = part["data"].get("delta")
                            if delta:
                                yield event("text", TextEvent, delta=delta)
                        elif part["type"] == "updates":
                            if final_answer is not None:
                                raise ValueError(
                                    "Received multiple answer-node results."
                                )
                            final_answer = _answer_from_update(part["data"])
                if final_answer is None:
                    raise ValueError("No completed answer-node result was received.")
            yield event("complete", CompleteEvent, answer=final_answer)
        except TimeoutError:
            yield event(
                "error",
                ErrorEvent,
                code="timeout",
                message="The answer exceeded the request deadline.",
                retryable=True,
            )
        except AgentError as error:
            yield event(
                "error",
                ErrorEvent,
                code=error.code,
                message=error.message,
                retryable=error.retryable,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            yield event(
                "error",
                ErrorEvent,
                code="persistence_error",
                message="The answer could not be saved or completed.",
                retryable=True,
            )
        finally:
            self.admission.release(str(conversation_id))
