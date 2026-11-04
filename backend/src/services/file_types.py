"""File types accepted by the personal file library.

The server decides a file's type from its extension and its first bytes; the
MIME type sent by the browser is never trusted.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path


DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class UnsupportedFileType(ValueError):
    """The file is not an accepted type, or its content does not match its extension."""


@dataclass(frozen=True)
class _FileType:
    content_type: str
    magic: bytes
    # Office files are ZIP archives; this member separates them from other archives.
    zip_member: str | None = None


_ZIP = b"PK\x03\x04"
_JPEG = _FileType("image/jpeg", b"\xff\xd8\xff")
ACCEPTED_TYPES: dict[str, _FileType] = {
    ".pdf": _FileType("application/pdf", b"%PDF-"),
    ".png": _FileType("image/png", b"\x89PNG\r\n\x1a\n"),
    ".jpg": _JPEG,
    ".jpeg": _JPEG,
    ".docx": _FileType(DOCX_CONTENT_TYPE, _ZIP, "word/document.xml"),
    ".xlsx": _FileType(XLSX_CONTENT_TYPE, _ZIP, "xl/workbook.xml"),
}


def detect_file_type(filename: str, path: Path) -> str:
    """Return the content type of a stored upload after checking it is accepted.

    The extension (compared case-insensitively) selects the expected type. The
    file must start with that type's signature bytes, and DOCX/XLSX archives
    must contain their main document part.

    Args:
        filename: Original client filename; only its last extension is used.
        path: Location of the uploaded bytes on disk.

    Returns:
        The server-chosen content type, such as `application/pdf`.

    Raises:
        UnsupportedFileType: The extension is not accepted, or the content does
            not match it.
    """

    expected = ACCEPTED_TYPES.get(Path(filename).suffix.lower())
    if expected is None:
        raise UnsupportedFileType("Only PDF, XLSX, DOCX, JPEG and PNG files are accepted")
    with path.open("rb") as source:
        head = source.read(len(expected.magic))
    if head != expected.magic:
        raise UnsupportedFileType("The file content does not match its extension")
    if expected.zip_member is not None:
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
        # Damaged archives raise more than BadZipFile, such as NotImplementedError
        # for unknown compression or UnicodeDecodeError (a ValueError) for names.
        except (zipfile.BadZipFile, NotImplementedError, ValueError, EOFError) as exc:
            raise UnsupportedFileType("The file content does not match its extension") from exc
        if expected.zip_member not in names:
            raise UnsupportedFileType("The file content does not match its extension")
    return expected.content_type
