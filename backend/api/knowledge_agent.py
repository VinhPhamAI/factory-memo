"""HTTP streaming API for the knowledge agent's public trace."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from backend.src.agents.agent.service import (
    STREAM_EVENT_VERSION,
    AgentStreamEvent,
    KnowledgeAgentError,
    KnowledgeAgentService,
)


router = APIRouter(prefix="/v1/knowledge-agent", tags=["knowledge-agent"])


class KnowledgeAgentStreamRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    thread_id: str = Field(min_length=1, max_length=200)

    @field_validator("query", "thread_id", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def encode_sse(event: AgentStreamEvent) -> str:
    """Encode one normalized event using the Server-Sent Events wire format."""

    event_id = f"{event.request_id}:{event.sequence}"
    payload = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event.event}\ndata: {payload}\n\n"


def _failure_event(
    *,
    request_id: str,
    thread_id: str,
    sequence: int,
    error: KnowledgeAgentError | Exception,
) -> AgentStreamEvent:
    if isinstance(error, KnowledgeAgentError):
        detail = error.public_detail()
    else:
        detail = {
            "message": "Knowledge agent request failed",
            "error_type": type(error).__name__,
        }
    return AgentStreamEvent(
        version=STREAM_EVENT_VERSION,
        sequence=sequence,
        event="run.failed",
        request_id=request_id,
        thread_id=thread_id,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        data=detail,
    )


def _service(request: Request) -> KnowledgeAgentService:
    service = getattr(request.app.state, "knowledge_agent", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Knowledge agent is not initialized")
    return service


@router.post("/stream", response_class=StreamingResponse)
async def stream_knowledge_agent(
    request: Request,
    payload: Annotated[KnowledgeAgentStreamRequest, Body(...)],
) -> StreamingResponse:
    service = _service(request)
    session = service.start_stream(payload.query, thread_id=payload.thread_id)

    async def event_source():
        last_sequence = 0
        try:
            async for event in session.events:
                last_sequence = event.sequence
                yield encode_sse(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield encode_sse(
                _failure_event(
                    request_id=session.request_id,
                    thread_id=session.thread_id,
                    sequence=last_sequence + 1,
                    error=exc,
                )
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
