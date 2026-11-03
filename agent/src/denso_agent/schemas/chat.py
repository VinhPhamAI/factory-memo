"""Define accepted chat input, saved history and normalized HTTP errors."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    """Accept one caller-owned conversation and a bounded message."""

    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    message: str = Field(min_length=1, max_length=20000)

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value):
        """Strip surrounding whitespace before enforcing message length.

        Args:
            value: Untrusted JSON message value.
        Returns:
            Trimmed text, or the original non-string for type validation.
        """
        return value.strip() if isinstance(value, str) else value


class HistoryMessage(BaseModel):
    """One saved user input or completed assistant answer."""

    role: Literal["user", "assistant"]
    text: str


class ChatHistory(BaseModel):
    """Saved public messages of a conversation in chronological order.

    `truncated` is true when older messages were left out to fit the
    configured response limit.
    """

    conversation_id: UUID
    messages: list[HistoryMessage]
    truncated: bool


class ErrorDetail(BaseModel):
    """Expose a stable error code and a sanitized explanation."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Wrap HTTP errors independently of framework validation internals."""

    error: ErrorDetail
