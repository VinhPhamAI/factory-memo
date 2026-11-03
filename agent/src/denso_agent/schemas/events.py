"""Define public events without exposing internal graph structures."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from denso_agent.schemas.citations import Citation, SearchStatus


class Event(BaseModel):
    """Identify an event's request, conversation and stream position."""

    request_id: UUID
    conversation_id: UUID
    sequence: int = Field(ge=1)


class StatusEvent(Event):
    """Acknowledge that a request has been admitted for generation."""

    state: Literal["generating"] = "generating"


class ReasoningEvent(Event):
    """Deliver the model's reasoning text, shown as unverified "thinking".

    It is never part of the answer and is not saved.
    """

    delta: str


class TextEvent(Event):
    """Deliver provisional answer text until completion is acknowledged."""

    delta: str


class CompleteEvent(Event):
    """Return the full answer after generation and checkpoint saving succeed."""

    finish_reason: Literal["stop"] = "stop"
    answer: str
    citations: list[Citation] = []
    search: SearchStatus = "not_used"


class ErrorEvent(Event):
    """Describe a failed answer without exposing private diagnostics."""

    code: str
    message: str
    retryable: bool
