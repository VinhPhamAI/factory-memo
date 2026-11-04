"""HTTP endpoints of the personal file library under /v1/files.

Every route requires a bearer access token. A file that is unknown, deleted
or owned by another user returns the same 404, so file IDs cannot be probed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from backend.api.auth import require_current_user
from backend.src.services.file_library import FileLibraryService, FileNotFound, InvalidFilename
from backend.src.services.file_types import UnsupportedFileType
from backend.src.storage.auth import Account
from backend.src.storage.file_library import EmptyFile, FileRecord, FileTooLarge


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/files", tags=["files"])

# Room for multipart boundaries and headers around one file.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class FileMetadata(BaseModel):
    """Public fields of a file; the storage location is never exposed."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime

    @classmethod
    def of(cls, record: FileRecord) -> FileMetadata:
        """Copy the public fields of a stored record, leaving out its storage key."""

        return cls(
            id=record.id,
            filename=record.filename,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            created_at=record.created_at,
        )


class FileList(BaseModel):
    """The caller's files, newest first."""

    files: list[FileMetadata]


def get_file_library(request: Request) -> FileLibraryService:
    """Return the configured file library, or fail with 503 when there is none."""

    library = getattr(request.app.state, "file_library", None)
    if library is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The file library is not configured")
    return library


User = Annotated[Account, Depends(require_current_user)]
Library = Annotated[FileLibraryService, Depends(get_file_library)]
NOT_FOUND = "File not found"


@router.post("", status_code=status.HTTP_201_CREATED, response_model=FileMetadata)
async def upload_file(request: Request, user: User, library: Library) -> FileMetadata:
    """Upload one file in the multipart field `file`.

    The form is parsed only after the caller is authenticated and the declared
    body size fits. Starlette spools a whole file part to disk before the size
    limit applies, so the body must be bounded by `Content-Length`: a request
    without one, or with any `Transfer-Encoding` (which takes precedence over
    `Content-Length` and lets the body exceed it), is refused with 411.
    """

    declared = request.headers.get("content-length", "")
    if "transfer-encoding" in request.headers or not declared.isdigit():
        raise HTTPException(status.HTTP_411_LENGTH_REQUIRED, "A Content-Length header is required")
    if int(declared) > library.max_bytes + MULTIPART_OVERHEAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The file is too large")
    async with request.form(max_files=1, max_fields=10) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A file is required")
        try:
            record = await library.upload(user, upload.filename or "", upload.file)
        except FileTooLarge as exc:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The file is too large") from exc
        except UnsupportedFileType as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
        except (EmptyFile, InvalidFilename) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except OSError as exc:
            logger.warning("Could not store an uploaded file: %s", type(exc).__name__)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not store the file") from exc
    return FileMetadata.of(record)


@router.get("", response_model=FileList)
async def list_files(user: User, library: Library) -> FileList:
    """List the caller's files, newest first."""

    return FileList(files=[FileMetadata.of(record) for record in await library.list(user)])


@router.get("/{file_id}", response_model=FileMetadata)
async def get_file(file_id: UUID, user: User, library: Library) -> FileMetadata:
    """Return the metadata of one of the caller's files."""

    try:
        return FileMetadata.of(await library.get(user, file_id))
    except FileNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from exc


@router.get("/{file_id}/content")
async def download_file(file_id: UUID, user: User, library: Library) -> FileResponse:
    """Send the original bytes as an attachment, never for display or execution."""

    try:
        content = await library.open_content(user, file_id)
    except FileNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from exc
    return FileResponse(
        content.path,
        media_type=content.record.content_type,
        filename=content.record.filename,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: UUID, user: User, library: Library) -> Response:
    """Remove one of the caller's files from their library."""

    try:
        await library.delete(user, file_id)
    except FileNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
