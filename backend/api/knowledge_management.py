"""Shared source lifecycle and human review API."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.api.knowledge_bases import refresh_catalog
from backend.api.voice_capture import ReviewRequest
from backend.src.storage import StorageError


router = APIRouter(prefix="/v1/knowledge-management", tags=["knowledge-management"])
Kind = Literal["upload", "conversation", "document"]


def normalized_id(kind: Kind, value: str) -> str:
    if kind == "document":
        return value
    try:
        identifier = UUID(value)
    except ValueError as exc:
        raise HTTPException(422, "Invalid source ID") from exc
    return identifier.hex if kind == "upload" else str(identifier)


async def service(request: Request, kb: str):
    await refresh_catalog(request)
    manager = request.app.state.knowledge_management
    manager.spec(kb)
    return manager


async def result(awaitable):
    try:
        return await awaitable
    except FileNotFoundError as exc:
        raise HTTPException(404, "Source file not found") from exc
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, StorageError) as exc:
        raise HTTPException(503, "Source storage is unavailable") from exc


@router.get("/{kb}/sources")
async def sources(request: Request, kb: str, offset: int = Query(0, ge=0)):
    manager = await service(request, kb)
    return await result(manager.inventory(kb, offset))


@router.get("/{kb}/sources/{kind}/{source_id}")
async def source(request: Request, kb: str, kind: Kind, source_id: str):
    manager = await service(request, kb)
    return await result(manager.detail(kb, kind, normalized_id(kind, source_id)))


@router.get("/{kb}/sources/upload/{source_id}/original")
async def original(request: Request, kb: str, source_id: UUID):
    manager = await service(request, kb)
    try:
        document = manager.uploads.get(kb, source_id.hex)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Source file not found") from exc
    return FileResponse(manager.uploads.object_dir(kb, source_id.hex) / "content",
                        filename=document.filename, media_type="application/octet-stream")


@router.post("/{kb}/sources/upload/{source_id}/convert", status_code=202)
async def convert(request: Request, kb: str, source_id: UUID):
    """Queues conversion only; the convert worker runs it."""

    manager = await service(request, kb)
    return await result(manager.convert(kb, source_id.hex))


class ExtractRequest(BaseModel):
    mode: Literal["knowledge", "documents"]
    # Extract even though the content nearly matches an extracted source.
    confirm_overlap: bool = False


@router.post("/{kb}/sources/{kind}/{source_id}/extract", status_code=202)
async def extract(request: Request, kb: str, kind: Kind, source_id: str, body: ExtractRequest):
    """Queues extraction; a raw upload is queued for conversion, then extraction."""

    manager = await service(request, kb)
    queued = await result(manager.extract(kb, kind, normalized_id(kind, source_id), body.mode,
                                          confirm_overlap=body.confirm_overlap))
    return {"status": "queued", "mode": body.mode, **queued}


@router.post("/{kb}/knowledge/{record_id}/review")
async def review(request: Request, kb: str, record_id: UUID, body: ReviewRequest):
    manager = await service(request, kb)
    return await result(manager.review_record(kb, record_id, body.decision, body.note))


class ChunkReviewRequest(ReviewRequest):
    chunk_id: str = Field(min_length=1, max_length=2000)


@router.post("/{kb}/sources/{kind}/{source_id}/review-chunk")
async def review_chunk(request: Request, kb: str, kind: Kind, source_id: str, body: ChunkReviewRequest):
    manager = await service(request, kb)
    return await result(manager.review_chunk(kb, kind, normalized_id(kind, source_id), body.chunk_id, body.decision, body.note))
