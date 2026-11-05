"""Storage of the personal file library: bytes on local disk, records in `files`.

Bytes live at `<root>/<storage key>`, where the key is a random 32-character
hex name. Client filenames are metadata only and never become paths. Records
live in the application database (APP_DATABASE_URL). Knowledge-source uploads
share `files` but use a different storage-key namespace and are excluded here.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.storage.app_database import AppDatabaseUnavailable, app_transaction
from backend.src.storage.orm import File


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_FILE_BYTES = 100 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_STORAGE_KEY = re.compile(r"[0-9a-f]{32}")
# KB originals have nested keys under DOCUMENT_UPLOAD_DIR, not FILE_LIBRARY_DIR.
_LIBRARY_FILE = File.storage_key.regexp_match(f"^{_STORAGE_KEY.pattern}$")


def file_library_dir_from_env() -> Path:
    """Return the storage root: FILE_LIBRARY_DIR, or `<project>/data/files` when unset."""

    raw = os.getenv("FILE_LIBRARY_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else PROJECT_ROOT / "data" / "files"


class FileTooLarge(ValueError):
    """The upload is larger than the allowed size."""


class EmptyFile(ValueError):
    """The upload has no content."""


class FileStorageUnavailable(AppDatabaseUnavailable):
    """The application database could not be reached; the API answers 503."""


@dataclass(frozen=True)
class TempUpload:
    """An upload copied to a temporary file that is not yet published."""

    path: Path
    size_bytes: int
    sha256: str


class FileBlobStore:
    """Stores file bytes on local disk, one file per storage key."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root if root is not None else file_library_dir_from_env()).resolve()

    def save_temp(self, source: BinaryIO, *, max_bytes: int = MAX_FILE_BYTES) -> TempUpload:
        """Copy an upload stream to a temporary file inside the storage root.

        The copy stops as soon as the size passes `max_bytes`. The temporary
        file sits in the root, so publishing it is a rename on the same disk.

        Args:
            source: Readable binary stream of the uploaded bytes.
            max_bytes: Largest accepted size in bytes (inclusive).

        Returns:
            The temporary file with its size and SHA-256 hex digest.

        Raises:
            FileTooLarge: The stream is larger than `max_bytes`.
            EmptyFile: The stream has no bytes.
            OSError: Reading the stream or writing the disk failed.
        """

        self.root.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".upload-", dir=self.root)
        path = Path(name)
        try:
            size = 0
            digest = hashlib.sha256()
            with os.fdopen(handle, "wb") as target:
                while chunk := source.read(_CHUNK_BYTES):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLarge(f"The file exceeds the {max_bytes}-byte limit")
                    target.write(chunk)
                    digest.update(chunk)
            if size == 0:
                raise EmptyFile("The file is empty")
        except BaseException:
            # Also removes partial files after disk errors or an interrupted copy.
            path.unlink(missing_ok=True)
            raise
        return TempUpload(path=path, size_bytes=size, sha256=digest.hexdigest())

    def publish(self, upload: TempUpload) -> str:
        """Move a temporary upload to its final location and return its new storage key."""

        key = uuid4().hex
        upload.path.rename(self.root / key)
        return key

    def discard(self, upload: TempUpload) -> None:
        """Delete an unpublished temporary upload; does nothing if it is already gone."""

        upload.path.unlink(missing_ok=True)

    def path(self, key: str) -> Path:
        """Return the location of published bytes.

        Raises:
            ValueError: The key is not a 32-character lowercase hex name.
        """

        if not _STORAGE_KEY.fullmatch(key):
            raise ValueError("Invalid storage key")
        return self.root / key

    def remove(self, key: str) -> None:
        """Delete published bytes; does nothing if they are already gone."""

        self.path(key).unlink(missing_ok=True)


@dataclass(frozen=True)
class FileRecord:
    """One row of the `files` table."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    sha256: str
    uploaded_by: UUID
    created_at: datetime
    deleted_at: datetime | None


def _record(row: File) -> FileRecord:
    return FileRecord(
        id=row.id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        storage_key=row.storage_key,
        sha256=row.sha256,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


class FileRecordStore:
    """Reads and writes `files` rows through short-lived transactions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        """Open one transaction; connection failures become FileStorageUnavailable."""

        async with app_transaction(self._sessions, FileStorageUnavailable) as session:
            yield session

    async def insert(
        self,
        *,
        filename: str,
        content_type: str,
        size_bytes: int,
        storage_key: str,
        sha256: str,
        uploaded_by: UUID,
    ) -> FileRecord:
        """Insert a file record and return it with its generated ID and dates.

        Raises:
            FileStorageUnavailable: The database could not be reached.
        """

        row = File(
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_key=storage_key,
            sha256=sha256,
            uploaded_by=uploaded_by,
        )
        async with self._transaction() as session:
            session.add(row)
        return _record(row)

    async def get(self, file_id: UUID) -> FileRecord | None:
        """Return a personal-library record, including a deleted one, or None.

        Knowledge-source files live in a separate byte store and are excluded.
        Callers decide access with the record's owner and deletion time.
        """

        async with self._transaction() as session:
            row = await session.scalar(select(File).where(File.id == file_id, _LIBRARY_FILE))
            return None if row is None else _record(row)

    async def list_owned(self, user_id: UUID) -> list[FileRecord]:
        """Return a user's non-deleted personal-library files, newest first."""

        async with self._transaction() as session:
            rows = (
                await session.scalars(
                    select(File)
                    .where(File.uploaded_by == user_id, File.deleted_at.is_(None), _LIBRARY_FILE)
                    .order_by(File.created_at.desc(), File.id.desc())
                )
            ).all()
            return [_record(row) for row in rows]

    async def soft_delete(self, file_id: UUID, user_id: UUID) -> bool:
        """Set `deleted_at` on a user's non-deleted personal-library file.

        Returns:
            True when a row changed; False when the file is unknown, belongs
            to another user or is already deleted.
        """

        async with self._transaction() as session:
            result = await session.execute(
                update(File)
                .where(
                    File.id == file_id, File.uploaded_by == user_id,
                    File.deleted_at.is_(None), _LIBRARY_FILE,
                )
                .values(deleted_at=func.now())
            )
            return result.rowcount == 1
