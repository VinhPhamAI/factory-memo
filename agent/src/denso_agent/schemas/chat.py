"""Define accepted chat input, saved history and normalized HTTP errors."""

import base64
import binascii
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from denso_agent.schemas.citations import Citation, SearchScope

MAX_IMAGE_BYTES = 3 * 1024 * 1024


class AttachmentRef(BaseModel):
    """Stable reference to a photo the caller authorized; saved with the message."""

    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=1, max_length=200)
    media_type: Literal["image/jpeg", "image/png", "image/webp"]


class ImageAttachment(AttachmentRef):
    """A photo for the current message: its reference and base64 bytes.

    The bytes are given to the model for this turn only and are never saved.
    """

    data: str = Field(min_length=1, repr=False)

    @field_validator("data")
    @classmethod
    def bounded_base64(cls, value: str) -> str:
        """Require strict base64 of at most MAX_IMAGE_BYTES decoded bytes.

        Args:
            value: Untrusted base64 text.
        Returns:
            The unchanged text.
        Raises:
            ValueError: Not base64, or the image is too large.
        """
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Image data must be base64.") from exc
        if len(decoded) > MAX_IMAGE_BYTES:
            raise ValueError("The image exceeds 3 MiB.")
        return value


class ChatRequest(BaseModel):
    """Accept one caller-owned conversation, a bounded message and search scope.

    Without `search_scope` the model is not offered knowledge search.
    """

    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    message: str = Field(min_length=1, max_length=20000)
    search_scope: SearchScope | None = None
    attachment: ImageAttachment | None = None

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
    citations: list[Citation] = []
    attachment: AttachmentRef | None = None


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
