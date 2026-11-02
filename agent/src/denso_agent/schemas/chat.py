"""Define accepted chat input and normalized HTTP errors."""

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


class ErrorDetail(BaseModel):
    """Expose a stable error code and a sanitized explanation."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Wrap HTTP errors independently of framework validation internals."""

    error: ErrorDetail
