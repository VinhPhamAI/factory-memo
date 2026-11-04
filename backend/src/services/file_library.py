"""Rules of the personal file library: upload, list, read and delete own files.

Read access is decided only by `FileLibraryService.can_read`. Deleting a file
hides it from its owner but keeps the record and the bytes, so readers of
existing citations or inquiries can still open the original once those
features grant access in `can_read`.
"""

from __future__ import annotations

import asyncio
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from backend.src.services.file_types import detect_file_type
from backend.src.storage.auth import Account
from backend.src.storage.file_library import (
    MAX_FILE_BYTES,
    FileBlobStore,
    FileRecord,
    FileRecordStore,
)


MAX_FILENAME_CHARS = 255
# Bidirectional controls can make a name display differently from its bytes,
# for example "invoice\u202efdp.exe" shown as "invoiceexe.pdf".
_BIDI_CONTROLS = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


class InvalidFilename(ValueError):
    """The filename is empty, too long or contains control characters."""


class FileNotFound(LookupError):
    """The file does not exist, is deleted, or the user may not access it."""


@dataclass(frozen=True)
class StoredBytes:
    """Published bytes of an upload that has passed the type check."""

    storage_key: str
    content_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class FileContent:
    """A readable file: its record and the location of its bytes on disk."""

    record: FileRecord
    path: Path


def validate_filename(filename: str) -> None:
    """Check a client filename that will be kept as metadata.

    Raises:
        InvalidFilename: The name is blank, longer than 255 characters, or
            contains a control character (such as a line break or NUL) or a
            bidirectional text control.
    """

    if not filename.strip() or len(filename) > MAX_FILENAME_CHARS:
        raise InvalidFilename(f"A filename of 1-{MAX_FILENAME_CHARS} characters is required")
    if any(unicodedata.category(char) == "Cc" or char in _BIDI_CONTROLS for char in filename):
        raise InvalidFilename("The filename must not contain control characters")


class FileLibraryService:
    """Manages files that each user uploads to their own library."""

    def __init__(
        self, records: FileRecordStore, blobs: FileBlobStore, *, max_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        self.records = records
        self.blobs = blobs
        self.max_bytes = max_bytes

    def _store_bytes(self, filename: str, source: BinaryIO) -> StoredBytes:
        """Copy, type-check and publish an upload; on failure nothing stays on disk.

        Runs in one worker thread, so a cancelled request cannot interrupt it
        between its steps.
        """

        upload = self.blobs.save_temp(source, max_bytes=self.max_bytes)
        try:
            content_type = detect_file_type(filename, upload.path)
            key = self.blobs.publish(upload)
        except BaseException:
            self.blobs.discard(upload)
            raise
        return StoredBytes(key, content_type, upload.size_bytes, upload.sha256)

    async def upload(self, user: Account, filename: str, source: BinaryIO) -> FileRecord:
        """Store an uploaded file in the user's library.

        The bytes are copied to a temporary file, type-checked, published
        under a random storage key and then recorded. A failure before the
        record is saved removes the bytes. If the request is cancelled, for
        example because the client disconnected, bytes may remain without a
        record; they are unreachable. Bytes are never removed once the record
        may have been committed.

        Args:
            user: The signed-in uploader, who becomes the owner.
            filename: Original client filename, kept as metadata only.
            source: Readable binary stream of the uploaded bytes.

        Returns:
            The new file record.

        Raises:
            InvalidFilename: The filename is not acceptable.
            FileTooLarge: The file is larger than `max_bytes`.
            EmptyFile: The file has no content.
            UnsupportedFileType: The type is not accepted or does not match the content.
            FileStorageUnavailable: The database could not be reached.
            OSError: The disk could not be written.
        """

        validate_filename(filename)
        # A worker thread keeps running after the awaiting request is cancelled.
        stored = await asyncio.to_thread(self._store_bytes, filename, source)
        try:
            return await self.records.insert(
                filename=filename,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
                storage_key=stored.storage_key,
                sha256=stored.sha256,
                uploaded_by=user.id,
            )
        except asyncio.CancelledError:
            # The commit may already have succeeded; a record whose bytes were
            # removed would stay listed but never download.
            raise
        except BaseException:
            await asyncio.to_thread(self.blobs.remove, stored.storage_key)
            raise

    async def list(self, user: Account) -> list[FileRecord]:
        """Return the user's files that are not deleted, newest first."""

        return await self.records.list_owned(user.id)

    async def get(self, user: Account, file_id: UUID) -> FileRecord:
        """Return a file record the user may read.

        Raises:
            FileNotFound: The file is unknown or `can_read` denies it.
        """

        record = await self.records.get(file_id)
        if record is None or not self.can_read(record, user):
            raise FileNotFound(str(file_id))
        return record

    async def open_content(self, user: Account, file_id: UUID) -> FileContent:
        """Return a readable file's record and the location of its bytes.

        Raises:
            FileNotFound: The file is unknown, `can_read` denies it, or its
                bytes are missing from disk.
        """

        record = await self.get(user, file_id)
        path = self.blobs.path(record.storage_key)
        if not await asyncio.to_thread(path.is_file):
            raise FileNotFound(str(file_id))
        return FileContent(record=record, path=path)

    async def delete(self, user: Account, file_id: UUID) -> None:
        """Hide the user's own file from their library; the record and bytes stay.

        Raises:
            FileNotFound: The file is unknown, belongs to another user or is
                already deleted.
        """

        if not await self.records.soft_delete(file_id, user.id):
            raise FileNotFound(str(file_id))

    def can_read(self, record: FileRecord, user: Account) -> bool:
        """Decide whether a user may read a file's metadata and bytes.

        Currently only the owner may read a file, and only until it is deleted.
        When citations and inquiries are implemented, readers of a citation or
        inquiry that references the file are allowed here too, including after
        the owner deleted it.
        """

        return record.uploaded_by == user.id and record.deleted_at is None
