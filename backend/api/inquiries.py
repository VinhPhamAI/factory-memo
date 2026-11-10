"""HTTP endpoints for expert inquiries and the list of assignable experts.

Every route authenticates the bearer access credential. An inquiry that is
missing, deleted or not visible to the caller returns the same 404, so IDs
cannot be probed. See backend/src/services/inquiries.py for the access rule.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import require_current_user
from backend.src.services.demo_proposals import with_demo_proposal
from backend.src.services.demo_scenario import scripted_proposal
from backend.src.services.inquiries import (
    ConversationNotFound,
    IneligibleAssignee,
    InquiryContext,
    InquiryDetail,
    InquiryNotFound,
    InquiryService,
    InquirySummary,
    InvalidContext,
    InvalidTransition,
    UnreadableFile,
)
from backend.src.storage.auth import Account
from backend.src.storage.inquiries import InquiryEventRecord


router = APIRouter(prefix="/v1/inquiries", tags=["inquiries"])
experts_router = APIRouter(prefix="/v1/experts", tags=["inquiries"])

_NOT_FOUND = "Inquiry not found"
_TITLE_MAX = 200
_CONTENT_MAX = 4000
_CONTENT_REQUIRED = {"edited", "denied", "question", "reply"}


class CreateInquiryRequest(BaseModel):
    """A new draft built from a conversation the caller owns."""

    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    title: str = Field(default="", max_length=_TITLE_MAX)
    context: InquiryContext = Field(default_factory=InquiryContext)
    file_ids: list[UUID] = Field(default_factory=list, max_length=20)


class UpdateInquiryRequest(BaseModel):
    """Fields to change on a draft; omitted fields stay as they are."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=_TITLE_MAX)
    context: InquiryContext | None = None


class SubmitRequest(BaseModel):
    """The expert who receives the inquiry."""

    model_config = ConfigDict(extra="forbid")
    assigned_to: UUID


class InquirySummaryResponse(BaseModel):
    """An inquiry in a list."""

    id: UUID
    title: str
    status: str
    requested_by: UUID
    requester_name: str
    assigned_to: UUID | None
    assignee_name: str | None
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None
    stage: Literal["todo", "in_progress", "in_review", "done"]


class InquiryFileResponse(BaseModel):
    """An attached file; its storage location is never exposed."""

    file_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    event_id: UUID | None


class EventActor(BaseModel):
    """The user who recorded an event."""

    id: UUID
    display_name: str


class InquiryEventResponse(BaseModel):
    """A permanent response recorded on an inquiry."""

    id: UUID
    kind: str
    content: str | None
    actor: EventActor
    created_at: datetime


class EventRequest(BaseModel):
    """A review action or reply; the actor's role comes from the inquiry, not this body."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["accepted", "edited", "denied", "question", "reply"]
    content: str | None = Field(default=None, max_length=_CONTENT_MAX)


class InquiryResponse(InquirySummaryResponse):
    """An inquiry with its context, attached files and events."""

    conversation_id: UUID
    context: InquiryContext
    files: list[InquiryFileResponse]
    events: list[InquiryEventResponse]


class InquiryList(BaseModel):
    """Inquiries of one box, most recently updated first."""

    inquiries: list[InquirySummaryResponse]


class Expert(BaseModel):
    """An account that can be assigned an inquiry."""

    id: UUID
    display_name: str


class ExpertList(BaseModel):
    """Eligible experts ordered by name."""

    experts: list[Expert]


def get_inquiry_service(request: Request) -> InquiryService:
    """Return the configured service, or fail with 503 when there is none."""

    service = getattr(request.app.state, "inquiry_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Inquiries are not configured")
    return service


CurrentUser = Annotated[Account, Depends(require_current_user)]
Service = Annotated[InquiryService, Depends(get_inquiry_service)]


def _summary(summary: InquirySummary) -> InquirySummaryResponse:
    return InquirySummaryResponse(**vars(summary))


def _event(item: InquiryEventRecord) -> InquiryEventResponse:
    return InquiryEventResponse(
        id=item.id, kind=item.kind, content=item.content, created_at=item.created_at,
        actor=EventActor(id=item.actor_id, display_name=item.actor_name),
    )


def inquiry_response(detail: InquiryDetail) -> InquiryResponse:
    """Convert a stored inquiry with its files and events to the public response."""

    return InquiryResponse(
        **vars(detail.summary),
        conversation_id=detail.conversation_id,
        context=InquiryContext.model_validate(detail.context),
        files=[InquiryFileResponse(**vars(item)) for item in detail.files],
        events=[_event(item) for item in detail.events],
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def rule_violation(exc: Exception) -> HTTPException:
    """Map a rule violation to its 409 or 422 response with a stable code."""

    if isinstance(exc, InvalidTransition):
        code, http_status = "invalid_transition", status.HTTP_409_CONFLICT
    elif isinstance(exc, IneligibleAssignee):
        code, http_status = "ineligible_assignee", status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(exc, UnreadableFile):
        code, http_status = "unreadable_file", status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        code, http_status = "invalid_context", status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(http_status, {"code": code, "message": str(exc)})


# Service errors that `rule_violation` maps to a 409 or 422 response.
RULE_ERRORS = (InvalidTransition, IneligibleAssignee, UnreadableFile, InvalidContext)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_inquiry(body: CreateInquiryRequest, user: CurrentUser, service: Service) -> InquiryResponse:
    """Save a draft inquiry from an owned conversation; 404 for any other conversation."""

    try:
        detail = await service.create_draft(
            user, body.conversation_id, body.title.strip(), body.context, body.file_ids
        )
    except ConversationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found") from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc
    return inquiry_response(detail)


@router.get("")
async def list_inquiries(
    user: CurrentUser, service: Service, box: Literal["sent", "assigned"] = "sent"
) -> InquiryList:
    """List the caller's sent inquiries (drafts included) or assigned ones (never drafts), at most 200."""

    return InquiryList(inquiries=[_summary(item) for item in await service.list(user, box)])


@router.get("/{inquiry_id}")
async def get_inquiry(inquiry_id: UUID, user: CurrentUser, service: Service) -> InquiryResponse:
    """Open an inquiry as its requester, or as its assignee once submitted."""

    try:
        return inquiry_response(await service.get(user, inquiry_id))
    except InquiryNotFound as exc:
        raise _not_found() from exc


@router.patch("/{inquiry_id}")
async def update_inquiry(
    inquiry_id: UUID, body: UpdateInquiryRequest, user: CurrentUser, service: Service
) -> InquiryResponse:
    """Edit the requester's draft; 409 once it has been submitted."""

    try:
        title = None if body.title is None else body.title.strip()
        return inquiry_response(await service.update_draft(user, inquiry_id, title=title, context=body.context))
    except InquiryNotFound as exc:
        raise _not_found() from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc


@router.delete("/{inquiry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inquiry(inquiry_id: UUID, user: CurrentUser, service: Service) -> Response:
    """Soft-delete the requester's draft; 409 once it has been submitted."""

    try:
        await service.delete_draft(user, inquiry_id)
    except InquiryNotFound as exc:
        raise _not_found() from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{inquiry_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def attach_file(inquiry_id: UUID, file_id: UUID, user: CurrentUser, service: Service) -> Response:
    """Attach one of the caller's library files to their draft."""

    try:
        await service.attach_file(user, inquiry_id, file_id)
    except InquiryNotFound as exc:
        raise _not_found() from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{inquiry_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_file(inquiry_id: UUID, file_id: UUID, user: CurrentUser, service: Service) -> Response:
    """Remove a file from the caller's draft."""

    try:
        await service.detach_file(user, inquiry_id, file_id)
    except InquiryNotFound as exc:
        raise _not_found() from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{inquiry_id}/submit")
async def submit_inquiry(
    inquiry_id: UUID, body: SubmitRequest, user: CurrentUser, service: Service
) -> InquiryResponse:
    """Send the requester's draft to an eligible expert; status becomes `open`."""

    try:
        return inquiry_response(await service.submit(user, inquiry_id, body.assigned_to))
    except InquiryNotFound as exc:
        raise _not_found() from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc


@router.post("/{inquiry_id}/demo-proposal")
async def add_demo_proposal(inquiry_id: UUID, user: CurrentUser, service: Service) -> InquiryResponse:
    """Add the canned demo AI proposal to the requester's draft ticket (ticket board demo).

    The proposal replaces any earlier demo block in `proposed_fix` and is saved
    like a `PATCH`. It reads no knowledge and calls no model. 404 when the
    caller does not own the inquiry; 409 once it has been submitted. In
    `DEMO_MODE` the scripted demo ticket waits its scripted analysis time first.
    """

    try:
        detail = await service.get(user, inquiry_id)
        context = InquiryContext.model_validate(detail.context)
        scripted = scripted_proposal(detail.summary.title)
        if scripted is not None:
            await asyncio.sleep(scripted[1])
        context.proposed_fix = with_demo_proposal(
            context.proposed_fix, detail.summary.title, context.situation
        )
        return inquiry_response(await service.update_draft(user, inquiry_id, context=context))
    except InquiryNotFound as exc:
        raise _not_found() from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc


@router.post("/{inquiry_id}/events")
async def record_event(
    inquiry_id: UUID, body: EventRequest, user: CurrentUser, service: Service
) -> InquiryResponse:
    """Accept, edit, deny, question or reply; 409 when the status or caller's role does not allow it."""

    content = (body.content or "").strip() or None
    if content is None and body.kind in _CONTENT_REQUIRED:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "content_required", "message": f"'{body.kind}' needs content"},
        )
    try:
        return inquiry_response(await service.record_event(user, inquiry_id, body.kind, content))
    except InquiryNotFound as exc:
        raise _not_found() from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc


@router.get("/{inquiry_id}/files/{file_id}/content")
async def download_evidence(
    inquiry_id: UUID, file_id: UUID, user: CurrentUser, service: Service
) -> FileResponse:
    """Send an attached file as an attachment to the requester or assignee.

    Still works after the requester deleted the file from their library.
    """

    try:
        content = await service.open_evidence(user, inquiry_id, file_id)
    except InquiryNotFound as exc:
        raise _not_found() from exc
    return FileResponse(
        content.path,
        media_type=content.record.content_type,
        filename=content.record.filename,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@experts_router.get("")
async def list_experts(user: CurrentUser, service: Service) -> ExpertList:
    """List active experts that an inquiry can be submitted to."""

    return ExpertList(experts=[Expert(id=id_, display_name=name) for id_, name in await service.experts()])
