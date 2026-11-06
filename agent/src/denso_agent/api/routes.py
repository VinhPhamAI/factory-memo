"""Authenticate, validate and delegate requests to the agent runtime."""

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import SecretStr

from denso_agent.api.authentication import authenticate
from denso_agent.api.stream_response import AgentStreamResponse
from denso_agent.runtime.inquiry_draft import create_draft
from denso_agent.schemas.chat import ChatHistory, ChatRequest, ErrorResponse
from denso_agent.schemas.inquiry_draft import InquiryDraft, InquiryDraftError

router = APIRouter()
logger = logging.getLogger(__name__)

UNAVAILABLE = {"code": "unavailable", "message": "Agent service is unavailable."}


@router.get("/healthz")
async def health():
    """Return process health without accessing private resources."""
    return {"status": "ok"}


@router.get("/readyz", dependencies=[Depends(authenticate)])
async def ready(request: Request):
    """Probe the database without contacting the paid model.

    Args:
        request: Request whose application owns the runtime.
    Returns:
        A minimal readiness status with HTTP 200 or 503.
    """
    runtime = request.app.state.runtime
    if runtime is None or not await runtime.ready():
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@router.get(
    "/v1/agent/conversations/{conversation_id}/history",
    dependencies=[Depends(authenticate)],
    response_model=ChatHistory,
    responses={code: {"model": ErrorResponse} for code in (401, 422, 503)},
)
async def history(conversation_id: UUID, request: Request) -> ChatHistory:
    """Return the saved public messages of one conversation.

    Args:
        conversation_id: Conversation UUID chosen by the caller.
        request: Request carrying application-owned runtime resources.
    Returns:
        Ordered saved messages, newest within the configured limit.
    Raises:
        HTTPException: The runtime or checkpoint store is unavailable.
    """
    runtime = request.app.state.runtime
    if runtime is None:
        raise HTTPException(503, detail=UNAVAILABLE)
    try:
        return await runtime.load_history(conversation_id)
    except Exception as error:  # pylint: disable=broad-exception-caught
        # Log only the class name; driver messages can include connection details.
        logger.warning("History read failed: %s", type(error).__name__)
        raise HTTPException(503, detail=UNAVAILABLE) from error


@router.post(
    "/v1/agent/conversations/{conversation_id}/inquiry-draft",
    dependencies=[Depends(authenticate)],
    response_model=InquiryDraft,
    responses={
        code: {"model": ErrorResponse} for code in (401, 409, 422, 429, 502, 503)
    },
)
async def inquiry_draft(conversation_id: UUID, request: Request) -> InquiryDraft:
    """Draft an inquiry from the saved conversation with one model call.

    The conversation is reserved like a chat turn, so a draft cannot overlap
    an active answer and draft calls count toward request capacity.

    Args:
        conversation_id: Conversation UUID chosen by the caller.
        request: Request carrying application-owned runtime resources.
    Returns:
        Model-written fields plus citations and attachments from the history.
    Raises:
        HTTPException: 409/429 when busy or full, 422 for an empty conversation,
            502 when the draft fails, 503 when the runtime or store is unavailable.
    """
    runtime = request.app.state.runtime
    if runtime is None or runtime.provider is None:
        raise HTTPException(503, detail=UNAVAILABLE)
    key = str(conversation_id)
    runtime.admission.acquire(key)
    try:
        try:
            saved = await runtime.load_history(conversation_id)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("History read failed: %s", type(error).__name__)
            raise HTTPException(503, detail=UNAVAILABLE) from error
        try:
            return await create_draft(runtime.provider, saved)
        except InquiryDraftError as error:
            raise HTTPException(
                error.status, detail={"code": error.code, "message": error.message}
            ) from error
    finally:
        runtime.admission.release(key)


@router.post(
    "/v1/agent/stream",
    dependencies=[Depends(authenticate)],
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Typed status, text, complete or error SSE frames.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **{code: {"model": ErrorResponse} for code in (401, 409, 413, 422, 429, 503)},
    },
)
async def answer(body: ChatRequest, request: Request):
    """Reserve a conversation before opening its event stream.

    Args:
        body: Validated conversation identifier and current message.
        request: Request carrying application-owned runtime resources.
    Returns:
        A streamed sequence of typed agent events.
    Raises:
        HTTPException: Runtime is unavailable, conversation is busy or capacity is full.
    """
    runtime = request.app.state.runtime
    if runtime is None:
        raise HTTPException(503, detail=UNAVAILABLE)
    runtime.admission.acquire(str(body.conversation_id))
    credential = request.headers.get("X-Employee-Access-Token")
    try:
        return AgentStreamResponse(
            runtime.stream(
                body.conversation_id,
                body.message,
                uuid4(),
                body.search_scope,
                body.attachment,
                employee_access=SecretStr(credential) if credential else None,
            ),
            runtime.admission,
            str(body.conversation_id),
        )
    except BaseException:
        runtime.admission.release(str(body.conversation_id))
        raise
