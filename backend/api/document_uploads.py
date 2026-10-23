"""Document upload API. Stores original bytes and metadata.

Without `extract_mode` the upload stays raw. With it (Auto on the capture
page), conversion and extraction are queued for the job workers; the upload
is kept even when queueing fails, and can be processed later.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

from backend.api.knowledge_bases import refresh_catalog
from backend.src.storage.document_uploads import (
    DocumentTooLarge,
    DocumentUploadStore,
    StoredDocument,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/documents", tags=["documents"])


async def require_kb(request: Request, kb: str) -> str:
    await refresh_catalog(request)
    pool = request.app.state.knowledge_agents
    if kb not in pool.names():
        raise HTTPException(404, "Knowledge base not found")
    return kb


class UploadedDocument(StoredDocument):
    job: dict[str, Any] | None = None
    job_error: str | None = None


async def queue_processing(connection: HTTPConnection, kb: str, kind: str, source_id: str,
                           mode: str) -> tuple[dict[str, Any] | None, str | None]:
    """Queues the full pipeline for a stored source; reports, not raises, a failure."""

    try:
        queued = await connection.app.state.knowledge_management.extract(kb, kind, source_id, mode)
    except HTTPException as exc:
        return None, str(exc.detail)
    except Exception:
        logger.warning("Could not queue processing of %s %s in KB %s", kind, source_id, kb, exc_info=True)
        return None, "Không xếp được job xử lý; nguồn đã lưu ở trạng thái Raw, hãy xử lý lại trong Quản lý tri thức."
    return queued["job"], None


@router.post("/{kb}/uploads", status_code=201, response_model=UploadedDocument,
             response_model_exclude_unset=True)
async def upload_document(
    request: Request,
    kb: Annotated[str, Depends(require_kb)],
    file: Annotated[UploadFile, File()],
    extract_mode: Annotated[Literal["knowledge", "documents"] | None, Form()] = None,
) -> UploadedDocument:
    store: DocumentUploadStore = request.app.state.document_uploads
    try:
        document = await run_in_threadpool(
            store.save, kb, file.file,
            filename=file.filename or "", content_type=file.content_type or "",
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
    uploaded = UploadedDocument(**document.model_dump())
    if extract_mode is not None:
        uploaded.job, uploaded.job_error = await queue_processing(
            request, kb, "upload", document.id, extract_mode)
    return uploaded
