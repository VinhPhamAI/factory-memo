"""Authenticate, validate and delegate requests to the agent runtime."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from denso_agent.api.authentication import authenticate
from denso_agent.api.stream_response import AgentStreamResponse
from denso_agent.schemas.chat import ChatRequest, ErrorResponse

router = APIRouter()


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
        raise HTTPException(
            503,
            detail={"code": "unavailable", "message": "Agent service is unavailable."},
        )
    runtime.admission.acquire(str(body.conversation_id))
    try:
        return AgentStreamResponse(
            runtime.stream(body.conversation_id, body.message, uuid4()),
            runtime.admission,
            str(body.conversation_id),
        )
    except BaseException:
        runtime.admission.release(str(body.conversation_id))
        raise
