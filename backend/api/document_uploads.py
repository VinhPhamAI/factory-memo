"""Document upload API. Stores original bytes and metadata.

Without `extract_mode` the upload stays raw. With it (Auto on the capture
page), conversion and extraction are queued for the job workers; the upload
is kept even when queueing fails, and can be processed later.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

from backend.api.knowledge_bases import refresh_catalog
from backend.api.auth import require_current_user
from backend.src.services.auth import Account
from backend.src.storage.orm import SourceKind
from backend.src.storage.repositories.base import KnowledgeStorageUnavailable
from backend.src.storage.repositories.sources import NewFile, SourceRepository
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
    job: dict[str, Any] | None = None
    job_error: str | None = None


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


@router.post("/{kb}/uploads", status_code=201, response_model=UploadedDocument,
             response_model_exclude_unset=True)
async def upload_document(
    request: Request,
    account: Annotated[Account, Depends(require_current_user)],
    kb_ref: Annotated[tuple[str, UUID], Depends(require_kb)],
    file: Annotated[UploadFile, File()],
    extract_mode: Annotated[Literal["knowledge", "documents"] | None, Form()] = None,
) -> UploadedDocument:
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
        await sources.create(
            knowledge_base_id,
            kind=SourceKind.DOCUMENT,
            title=document.filename,
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
    uploaded = UploadedDocument(**document.model_dump())
    if extract_mode is not None:
        uploaded.job, uploaded.job_error = await queue_processing(
            request, kb, "upload", document.id, extract_mode)
    return uploaded
