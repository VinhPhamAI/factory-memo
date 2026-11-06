"""HTTP endpoints for a user's agent conversations.

Every route authenticates the bearer access credential and checks that the
conversation is active and owned by that user before calling the agent, so a
denied request never reaches it. Messages are stored by the agent; this API
stores only the conversation list. See docs/features/conversations.md.
"""

from __future__ import annotations

import logging
import unicodedata
from contextlib import aclosing
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, SecretStr, StringConstraints

from backend.api.auth import AccessCredential, require_current_user
from backend.api.inquiries import (
    RULE_ERRORS,
    InquiryResponse,
    inquiry_response,
    rule_violation,
)
from backend.src.clients.agent import AgentHistory, AgentRejected, AgentStream, AgentUnavailable
from backend.src.services.auth import Account
from backend.src.services.inquiries import InvalidAgentDraft
from backend.src.storage.conversations import ConversationNotFound, ConversationRecord, ConversationStore


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

_NOT_FOUND = "Conversation not found"


def _no_control_characters(value: str) -> str:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("must not contain control characters")
    return value


Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_no_control_characters),
]
# Same limit as the agent's ChatRequest.message.
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]


class TitleRequest(BaseModel):
    """Title of a new or renamed conversation."""

    model_config = ConfigDict(extra="forbid")
    title: Title


class MessageRequest(BaseModel):
    """The user's next message."""

    model_config = ConfigDict(extra="forbid")
    message: Message


class ConversationResponse(BaseModel):
    """A conversation in the user's list."""

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationList(BaseModel):
    """The user's active conversations, most recently active first."""

    conversations: list[ConversationResponse]


class ConversationStreamResponse(StreamingResponse):
    """Forward agent SSE bytes and close the upstream response on every exit.

    Closing the upstream response when the browser disconnects ends
    generation at the agent and releases its per-conversation slot.
    """

    def __init__(self, upstream: AgentStream) -> None:
        super().__init__(
            upstream.chunks(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        self._upstream = upstream

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            async with aclosing(self.body_iterator):
                await super().__call__(scope, receive, send)
        finally:
            await self._upstream.aclose()


def get_conversation_store(request: Request) -> ConversationStore:
    """Return the configured store, or fail with 503 when there is none."""

    store = getattr(request.app.state, "conversation_store", None)
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Conversations are not configured")
    return store


def get_agent_client(request: Request) -> Any:
    """Return the configured agent client, or fail with 503 when there is none."""

    client = getattr(request.app.state, "agent_client", None)
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The agent is not configured")
    return client


CurrentUser = Annotated[Account, Depends(require_current_user)]
Store = Annotated[ConversationStore, Depends(get_conversation_store)]


def _public(record: ConversationRecord) -> ConversationResponse:
    return ConversationResponse(
        id=record.id, title=record.title, created_at=record.created_at, updated_at=record.updated_at
    )


def _agent_unavailable(exc: AgentUnavailable) -> HTTPException:
    logger.warning("Agent call failed: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The agent is temporarily unavailable")


@router.get("")
async def list_conversations(user: CurrentUser, store: Store) -> ConversationList:
    """List the user's active conversations, newest activity first (at most 200)."""

    records = await store.list_owned(user.id)
    return ConversationList(conversations=[_public(record) for record in records])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(body: TitleRequest, user: CurrentUser, store: Store) -> ConversationResponse:
    """Create an empty conversation owned by the user."""

    return _public(await store.create(user.id, body.title))


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: UUID, body: TitleRequest, user: CurrentUser, store: Store
) -> ConversationResponse:
    """Rename an owned conversation; 404 for any other."""

    try:
        return _public(await store.rename(conversation_id, user.id, body.title))
    except ConversationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND) from exc


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: UUID, user: CurrentUser, store: Store) -> Response:
    """Hide an owned conversation. Its messages and inquiry links are kept."""

    try:
        await store.soft_delete(conversation_id, user.id)
    except ConversationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/history")
async def conversation_history(
    conversation_id: UUID,
    user: CurrentUser,
    store: Store,
    agent: Annotated[Any, Depends(get_agent_client)],
) -> AgentHistory:
    """Return the saved messages of an owned conversation from the agent."""

    try:
        await store.require_active_owner(conversation_id, user.id)
    except ConversationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND) from exc
    try:
        return await agent.fetch_history(conversation_id)
    except AgentUnavailable as exc:
        raise _agent_unavailable(exc) from exc


@router.post(
    "/{conversation_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def stream_answer(
    conversation_id: UUID,
    body: MessageRequest,
    user: CurrentUser,
    store: Store,
    agent: Annotated[Any, Depends(get_agent_client)],
    credentials: AccessCredential,
) -> Response:
    """Send the next message and forward the agent's events unchanged.

    Returns 409 while the conversation already has an active answer and 429
    when the agent is at capacity; nothing is sent again automatically.
    """

    try:
        # Each check closes its database session before the agent call starts.
        await store.require_active_owner(conversation_id, user.id)
    except ConversationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND) from exc
    try:
        upstream = await agent.open_stream(
            conversation_id,
            body.message,
            employee_access=SecretStr(credentials.credentials) if credentials is not None else None,
        )
    except AgentRejected as exc:
        return JSONResponse(
            {"detail": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code
        )
    except AgentUnavailable as exc:
        raise _agent_unavailable(exc) from exc
    try:
        # Only an accepted message moves the conversation up the list.
        await store.mark_active(conversation_id, user.id)
    except BaseException as exc:
        await upstream.aclose()
        if isinstance(exc, ConversationNotFound):
            raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND) from exc
        raise
    return ConversationStreamResponse(upstream)


@router.post("/{conversation_id}/inquiry-draft", status_code=status.HTTP_201_CREATED)
async def draft_inquiry_from_chat(
    conversation_id: UUID,
    request: Request,
    user: CurrentUser,
    store: Store,
    agent: Annotated[Any, Depends(get_agent_client)],
) -> InquiryResponse:
    """Draft an inquiry from an owned conversation and save it as a `draft` row.

    The agent writes the text; this route copies its citations and the photos
    the requester can still read into the saved context. Nothing is saved when
    the agent fails. Agent 422/409/429/502 keep their status with a
    `{detail: {code, message}}` body; an unreachable agent gives 503.
    """

    inquiries = getattr(request.app.state, "inquiry_service", None)
    if inquiries is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Inquiries are not configured")
    try:
        await store.require_active_owner(conversation_id, user.id)
    except ConversationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND) from exc
    try:
        draft = await agent.create_inquiry_draft(conversation_id)
    except AgentRejected as exc:
        return JSONResponse(
            {"detail": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code
        )
    except AgentUnavailable as exc:
        raise _agent_unavailable(exc) from exc

    try:
        detail = await inquiries.create_draft_from_agent(user, conversation_id, draft)
    except ConversationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND) from exc
    except InvalidAgentDraft as exc:
        logger.warning("Agent draft rejected by inquiry limits: %s", type(exc).__name__)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {"code": "draft_failed", "message": "The generated draft exceeds the inquiry limits"},
        ) from exc
    except RULE_ERRORS as exc:
        raise rule_violation(exc) from exc
    return inquiry_response(detail)
