"""Document upload API. Stores original bytes and metadata without extraction."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

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


@router.post("/{kb}/uploads", status_code=201, response_model=StoredDocument)
async def upload_document(
    request: Request,
    kb: Annotated[str, Depends(require_kb)],
    file: Annotated[UploadFile, File()],
) -> StoredDocument:
    store: DocumentUploadStore = request.app.state.document_uploads
    try:
        return await run_in_threadpool(
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
