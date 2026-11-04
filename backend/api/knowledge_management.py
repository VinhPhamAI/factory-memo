"""Shared source lifecycle and human review API."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.api.knowledge_bases import refresh_catalog
from backend.api.auth import require_current_user
from backend.api.knowledge_bases import require_expert
from backend.api.voice_capture import ReviewRequest
from backend.src.services.auth import Account
from backend.src.storage.repositories.base import KnowledgeStorageUnavailable
from backend.src.storage import StorageError


router = APIRouter(
    prefix="/v1/knowledge-management",
    tags=["knowledge-management"],
    dependencies=[Depends(require_current_user)],
)
Kind = Literal["upload", "conversation", "document"]


def normalized_id(kind: Kind, value: str) -> str:
    try:
        identifier = UUID(value)
    except ValueError as exc:
        raise HTTPException(422, "Invalid source ID") from exc
    return str(identifier)


async def service(request: Request, kb: str):
    """Resolve management for an active KB or report unavailable storage."""

    await refresh_catalog(request)
    manager = request.app.state.knowledge_management
    if manager is None:
        raise HTTPException(503, "Knowledge management is not configured")
    manager.spec(kb)
    return manager


async def result(awaitable):
    try:
        return await awaitable
    except FileNotFoundError as exc:
        raise HTTPException(404, "Source file not found") from exc
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, StorageError, KnowledgeStorageUnavailable) as exc:
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
    knowledge_base_id, _spec = manager.spec(kb)
    source = await manager.sources.get(knowledge_base_id, source_id)
    if source is None or source.file is None:
        raise HTTPException(404, "Source file not found")
    path = request.app.state.document_uploads.content_path(source.file.storage_key)
    if not path.is_file():
        raise HTTPException(404, "Source file not found")
    return FileResponse(path, filename=source.file.filename, media_type=source.file.content_type)


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
async def review(
    request: Request,
    kb: str,
    record_id: UUID,
    body: ReviewRequest,
    account: Annotated[Account, Depends(require_expert)],
):
    manager = await service(request, kb)
    return await result(manager.review_record(kb, record_id, body.decision, body.note, account.id))


class ChunkReviewRequest(ReviewRequest):
    chunk_id: str = Field(min_length=1, max_length=2000)


@router.post("/{kb}/sources/{kind}/{source_id}/review-chunk")
async def review_chunk(
    request: Request,
    kb: str,
    kind: Kind,
    source_id: str,
    body: ChunkReviewRequest,
    account: Annotated[Account, Depends(require_expert)],
):
    manager = await service(request, kb)
    return await result(manager.review_chunk(kb, kind, normalized_id(kind, source_id), body.chunk_id, body.decision, body.note, account.id))


@router.get("/{kb}/conflicts")
async def conflicts(request: Request, kb: str, status: str | None = None, evidence_id: UUID | None = None,
                    offset: int = Query(0, ge=0)):
    """Current judged pairs (one per pair) with both records, for review."""

    manager = await service(request, kb)
    return await result(manager.conflict_list(kb, status=status, evidence_id=evidence_id, offset=offset))


class ConflictReviewRequest(BaseModel):
    decision: Literal["confirm", "dismiss", "supersede"]
    note: str = Field(default="", max_length=1000)
    # supersede: the record kept; the other one is deprecated in its favour.
    keep_id: UUID | None = None


class ConflictRecheckRequest(BaseModel):
    record_ids: list[UUID] = Field(default_factory=list, max_length=200)


@router.post("/{kb}/conflicts/recheck", status_code=202)
async def recheck_conflicts(request: Request, kb: str, body: ConflictRecheckRequest):
    """Queues the conflict stage again for edited records (all changed ones without ids)."""

    manager = await service(request, kb)
    return await result(manager.recheck_conflicts(kb, body.record_ids))


@router.post(
    "/{kb}/conflicts/{conflict_id}/review",
    dependencies=[Depends(require_expert)],
)
async def review_conflict(request: Request, kb: str, conflict_id: UUID, body: ConflictReviewRequest):
    manager = await service(request, kb)
    return await result(manager.review_conflict(kb, conflict_id, body.decision, body.note, body.keep_id))
