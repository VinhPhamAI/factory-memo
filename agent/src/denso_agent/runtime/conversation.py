"""Bound active requests and translate graph progress into public events."""

import asyncio
import base64
from contextlib import aclosing
from typing import Awaitable, Callable
from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr

from denso_agent.api.sse import encode_event
from denso_agent.config import RuntimeSettings
from denso_agent.runtime.errors import AgentError
from denso_agent.runtime.graph import TurnContext, TurnImage
from denso_agent.runtime.history import bounded_history, saved_citations, saved_tools
from denso_agent.schemas.chat import (
    AttachmentRef,
    ChatHistory,
    ImageAttachment,
)
from denso_agent.schemas.citations import SearchScope
from denso_agent.schemas.events import (
    CompleteEvent,
    ErrorEvent,
    ReasoningEvent,
    StatusEvent,
    TextEvent,
    ToolEvent,
)


def _answer_from_update(update: object) -> AIMessage:
    """Extract the single completed assistant answer from a graph update.

    Args:
        update: LangGraph update containing the current answer node's result.

    Returns:
        The saved assistant message; its text keeps whitespace and Unicode.

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
    return messages[0]


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
        self,
        graph,
        ready: Callable[[], Awaitable[bool]],
        settings: RuntimeSettings,
        provider=None,
    ):
        """Connect the compiled graph, bounded readiness probe and request limits.

        Args:
            graph: Compiled LangGraph exposing asynchronous custom streaming.
            ready: Database readiness probe that makes no model call.
            settings: Validated runtime limits and credentials.
            provider: Open model client for non-graph calls such as inquiry
                drafts; None disables them.
        """
        self.provider = provider
        self.graph = graph
        self.ready = ready
        self.settings = settings
        self.admission = Admission(settings.max_concurrent_requests)

    async def load_history(self, conversation_id: UUID) -> ChatHistory:
        """Read the saved public messages of one conversation checkpoint.

        Only human inputs and assistant answers with text content are public;
        tool, system and other internal messages are left out. When the
        history exceeds the configured message or character limit, the newest
        messages that fit are returned in chronological order.

        Args:
            conversation_id: Checkpoint thread identifier.
        Returns:
            Ordered messages and whether older messages were omitted. A
            conversation without a checkpoint has no messages.
        Raises:
            Exception: The checkpoint store could not be read.
        """
        state = await self.graph.aget_state(
            {"configurable": {"thread_id": str(conversation_id)}}
        )
        return bounded_history(
            conversation_id,
            state.values.get("messages", []),
            self.settings.history_max_messages,
            self.settings.history_max_characters,
        )

    async def stream(
        self,
        conversation_id: UUID,
        message: str,
        request_id: UUID,
        search_scope: SearchScope | None = None,
        attachment: ImageAttachment | None = None,
        *,
        employee_access: SecretStr | None = None,
    ):
        """Stream provisional text, then return the full answer after persistence.

        Args:
            conversation_id: Caller-owned checkpoint thread identifier.
            message: Validated current human input.
            request_id: Server-generated tracing identifier.
            search_scope: Knowledge bases the caller authorized; None disables search.
            attachment: Optional photo; its reference is saved with the message
                and its bytes reach only this turn's model call.
            employee_access: Optional backend credential kept in request memory.
        Yields:
            Typed SSE frames; at most one connected terminal event.
        """
        sequence = 0
        final_answer: AIMessage | None = None

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
                    {
                        "messages": [
                            HumanMessage(
                                content=message,
                                additional_kwargs=(
                                    {
                                        "attachment": AttachmentRef(
                                            file_id=attachment.file_id,
                                            media_type=attachment.media_type,
                                        ).model_dump()
                                    }
                                    if attachment
                                    else {}
                                ),
                            )
                        ]
                    },
                    config={
                        "configurable": {
                            "thread_id": str(conversation_id),
                            "search_scope": search_scope,
                            # Bytes, not text: configuration strings can be
                            # copied into checkpoint metadata.
                            "image": (
                                TurnImage(
                                    attachment.media_type,
                                    base64.b64decode(attachment.data),
                                )
                                if attachment
                                else None
                            ),
                        }
                    },
                    stream_mode=["custom", "updates"],
                    context=TurnContext(
                        reply_id=request_id, employee_access=employee_access
                    ),
                    version="v2",
                    durability="sync",
                )
                async with aclosing(iterator):
                    async for part in iterator:
                        if part["type"] == "custom":
                            delta = part["data"].get("delta")
                            if delta:
                                yield event("text", TextEvent, delta=delta)
                            reasoning = part["data"].get("reasoning")
                            if reasoning:
                                yield event(
                                    "reasoning", ReasoningEvent, delta=reasoning
                                )
                            tool = part["data"].get("tool")
                            if tool:
                                yield event("tool", ToolEvent, tool=tool)
                        elif part["type"] == "updates":
                            if final_answer is not None:
                                raise ValueError(
                                    "Received multiple answer-node results."
                                )
                            final_answer = _answer_from_update(part["data"])
                if final_answer is None:
                    raise ValueError("No completed answer-node result was received.")
            yield event(
                "complete",
                CompleteEvent,
                answer=final_answer.content,
                citations=saved_citations(final_answer),
                search=final_answer.additional_kwargs.get("search", "not_used"),
                reply_id=final_answer.additional_kwargs.get("reply_id"),
                tools=saved_tools(final_answer),
            )
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
