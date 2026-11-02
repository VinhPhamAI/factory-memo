"""Define public events without exposing internal graph structures."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Identify an event's request, conversation and stream position."""

    request_id: UUID
    conversation_id: UUID
    sequence: int = Field(ge=1)


class StatusEvent(Event):
    """Acknowledge that a request has been admitted for generation."""

    state: Literal["generating"] = "generating"


class TextEvent(Event):
    """Deliver provisional answer text until completion is acknowledged."""

    delta: str


class CompleteEvent(Event):
    """Return the full answer after generation and checkpoint saving succeed."""

    finish_reason: Literal["stop"] = "stop"
    answer: str


class ErrorEvent(Event):
    """Describe a failed answer without exposing private diagnostics."""

    code: str
    message: str
    retryable: bool
