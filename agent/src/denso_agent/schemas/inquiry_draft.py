"""Define the model-written inquiry draft and the endpoint response."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from denso_agent.schemas.chat import AttachmentRef
from denso_agent.schemas.citations import Citation

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]


class InquiryDraftFields(BaseModel):
    """Model-written draft text; evidence is added from the saved history."""

    model_config = ConfigDict(extra="forbid")
    # May be empty: the requester must fill it in before submitting.
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
    situation: Text = ""
    observations: Text = ""
    attempted_actions: Text = ""
    proposed_fix: Text = ""


class InquiryDraft(BaseModel):
    """Draft text plus the citations and photos copied from the chat history."""

    fields: InquiryDraftFields
    citations: list[Citation]
    attachments: list[AttachmentRef]


class InquiryDraftError(Exception):
    """Draft could not be produced; `code` is the public error code."""

    def __init__(self, code: str, message: str, status: int):
        """Store the public code, safe message and HTTP status."""
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
