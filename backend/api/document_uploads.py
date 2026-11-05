"""Document upload API. Stores original bytes and metadata.

Capture attaches existing library files; direct-upload clients remain supported.
Without `extract_mode` the source stays raw. With it, conversion and extraction
are queued; a queue failure keeps the saved source available for later processing.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection
from pydantic import BaseModel

from backend.api.knowledge_bases import refresh_catalog
from backend.api.auth import require_current_user
from backend.api.files import get_file_library
from backend.src.services.file_library import FileLibraryService, FileNotFound
from backend.src.services.auth import Account
from backend.src.storage.repositories.base import KnowledgeStorageUnavailable
from backend.src.storage.repositories.sources import NewFile, SourceFileNotFound, SourceRepository
from backend.src.storage.document_uploads import (
    DocumentTooLarge,
    DocumentUploadStore,
    StoredDocument,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/documents", tags=["documents"])


async def require_kb(request: Request, kb: str) -> tuple[str, UUID]:
    """Resolve an active application knowledge base by URL name."""

    await refresh_catalog(request)
    catalog = request.app.state.kb_catalog
    if catalog is None:
        raise HTTPException(503, "The knowledge base catalog is not configured")
    resolved = catalog.resolve(kb)
    if resolved is None:
        raise HTTPException(404, "Knowledge base not found")
    return kb, resolved[0]


class UploadedDocument(StoredDocument):
    duplicate: bool = False
    job: dict[str, Any] | None = None
    job_error: str | None = None


class AttachLibraryFile(BaseModel):
    """An existing personal file to attach as a source, with optional Auto processing."""

    file_id: UUID
    extract_mode: Literal["knowledge", "documents"] | None = None


@router.post(
    "/{kb}/files", status_code=201, response_model=UploadedDocument,
    responses={200: {"model": UploadedDocument, "description": "Existing identical source"}},
)
async def attach_library_file(
    request: Request,
    response: Response,
    body: AttachLibraryFile,
    account: Annotated[Account, Depends(require_current_user)],
    kb_ref: Annotated[tuple[str, UUID], Depends(require_kb)],
    library: Annotated[FileLibraryService, Depends(get_file_library)],
) -> UploadedDocument:
    """Attach an owned library file to a KB and optionally queue conversion and extraction.

    Original bytes stay in the file library. A duplicate returns the existing source
    without queuing more jobs; attachment failures leave the library file intact.
    """

    kb, knowledge_base_id = kb_ref
    sources: SourceRepository = request.app.state.sources
    if sources is None:
        raise HTTPException(503, "Source storage is unavailable")
    try:
        await library.open_content(account, body.file_id)
        source, duplicate = await sources.create_from_file(
            knowledge_base_id, file_id=body.file_id, submitted_by=account.id,
        )
    except (FileNotFound, SourceFileNotFound) as exc:
        raise HTTPException(404, "File not found") from exc
    except OSError as exc:
        raise HTTPException(503, "File storage is unavailable") from exc
    original = source.file
    uploaded = UploadedDocument(
        id=source.id.hex, kb=kb, filename=original.filename,
        content_type=original.content_type, size_bytes=original.size_bytes,
        sha256=original.sha256, storage_key=original.storage_key,
        uploaded_at=original.created_at, status="stored", duplicate=duplicate,
    )
    if duplicate:
        response.status_code = 200
    elif body.extract_mode is not None:
        uploaded.job, uploaded.job_error = await queue_processing(
            request, kb, "upload", uploaded.id, body.extract_mode,
        )
    return uploaded


async def queue_processing(connection: HTTPConnection, kb: str, kind: str, source_id: str,
                           mode: str) -> tuple[dict[str, Any] | None, str | None]:
    """Queues the full pipeline for a stored source; reports, not raises, a failure.

    Near-duplicate content is still queued: the worker marks the source for
    the reviewer to confirm in Quản lý tri thức.
    """

    try:
        queued = await connection.app.state.knowledge_management.extract(
            kb, kind, source_id, mode, overlap_in_worker=True)
    except HTTPException as exc:
        detail = exc.detail
        return None, detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
    except Exception:
        logger.warning("Could not queue processing of %s %s in KB %s", kind, source_id, kb, exc_info=True)
        return None, "Không xếp được job xử lý; nguồn đã lưu ở trạng thái Raw, hãy xử lý lại trong Quản lý tri thức."
    return queued["job"], None


@router.post(
    "/{kb}/uploads", status_code=201, response_model=UploadedDocument,
    response_model_exclude_unset=True,
    responses={200: {"model": UploadedDocument, "description": "Existing identical upload"}},
)
async def upload_document(
    request: Request,
    response: Response,
    account: Annotated[Account, Depends(require_current_user)],
    kb_ref: Annotated[tuple[str, UUID], Depends(require_kb)],
    file: Annotated[UploadFile, File()],
    extract_mode: Annotated[Literal["knowledge", "documents"] | None, Form()] = None,
) -> UploadedDocument:
    """Save original bytes and return the existing source when the hash matches."""

    kb, knowledge_base_id = kb_ref
    store: DocumentUploadStore = request.app.state.document_uploads
    sources: SourceRepository = request.app.state.sources
    source_id = uuid4()
    try:
        document = await run_in_threadpool(
            store.save, kb, file.file,
            filename=file.filename or "", content_type=file.content_type or "",
            source_id=source_id,
        )
    except DocumentTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        logger.exception("Could not store a document for KB %s", kb)
        raise HTTPException(503, "Document storage is unavailable; please retry") from exc
    finally:
        await file.close()
    try:
        source, duplicate = await sources.create_upload(
            knowledge_base_id,
            submitted_by=account.id,
            source_id=source_id,
            file=NewFile(
                filename=document.filename,
                content_type=document.content_type,
                size_bytes=document.size_bytes,
                storage_key=document.storage_key,
                sha256=document.sha256,
            ),
        )
    except KnowledgeStorageUnavailable as exc:
        await run_in_threadpool(store.remove, document.storage_key)
        raise HTTPException(503, "Source storage is unavailable") from exc
    except Exception:
        await run_in_threadpool(store.remove, document.storage_key)
        raise
    if duplicate:
        try:
            await run_in_threadpool(store.remove, document.storage_key)
        except OSError as exc:
            logger.exception("Could not remove the redundant upload for KB %s", kb)
            raise HTTPException(503, "Document storage is unavailable; please retry") from exc
        response.status_code = 200
        original = source.file
        return UploadedDocument(
            id=source.id.hex, kb=kb, filename=original.filename,
            content_type=original.content_type, size_bytes=original.size_bytes,
            sha256=original.sha256, storage_key=original.storage_key,
            uploaded_at=original.created_at, status="stored", duplicate=True,
        )
    uploaded = UploadedDocument(**document.model_dump())
    if extract_mode is not None:
        uploaded.job, uploaded.job_error = await queue_processing(
            request, kb, "upload", document.id, extract_mode)
    return uploaded
