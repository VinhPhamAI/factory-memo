"""File types accepted by the personal file library.

The server decides a file's type from its extension and its first bytes; the
MIME type sent by the browser is never trusted.
"""

from __future__ import annotations

import codecs
import zipfile
from dataclasses import dataclass
from pathlib import Path


DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEXT_CONTENT_TYPE = "text/plain"
MARKDOWN_CONTENT_TYPE = "text/markdown"
TEXT_CONTENT_TYPES = frozenset({TEXT_CONTENT_TYPE, MARKDOWN_CONTENT_TYPE})
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_TEXT_CHUNK_BYTES = 1024 * 1024


class UnsupportedFileType(ValueError):
    """The file is not an accepted type, or its content does not match its extension."""


@dataclass(frozen=True)
class _FileType:
    content_type: str
    magic: bytes
    # Office files are ZIP archives; this member separates them from other archives.
    zip_member: str | None = None
    # Text has no signature; its bytes must decode as UTF-8 or BOM-marked UTF-16.
    text: bool = False


_ZIP = b"PK\x03\x04"
_JPEG = _FileType("image/jpeg", b"\xff\xd8\xff")
ACCEPTED_TYPES: dict[str, _FileType] = {
    ".pdf": _FileType("application/pdf", b"%PDF-"),
    ".png": _FileType("image/png", b"\x89PNG\r\n\x1a\n"),
    ".jpg": _JPEG,
    ".jpeg": _JPEG,
    ".docx": _FileType(DOCX_CONTENT_TYPE, _ZIP, "word/document.xml"),
    ".xlsx": _FileType(XLSX_CONTENT_TYPE, _ZIP, "xl/workbook.xml"),
    ".txt": _FileType(TEXT_CONTENT_TYPE, b"", text=True),
    ".md": _FileType(MARKDOWN_CONTENT_TYPE, b"", text=True),
}


def text_encoding(head: bytes) -> str:
    """Return the encoding the text converter uses for a file starting with `head`.

    Args:
        head: The first bytes of the file.

    Returns:
        `utf-16` when a UTF-16 byte order mark is present, otherwise `utf-8-sig`.
    """

    return "utf-16" if head.startswith(_UTF16_BOMS) else "utf-8-sig"


def _is_readable_text(path: Path) -> bool:
    """Check that the whole file decodes as text without NUL characters.

    Decodes in chunks with an incremental decoder, so a multi-byte character
    that straddles a chunk boundary is not mistaken for damage.
    """

    with path.open("rb") as source:
        first = source.read(_TEXT_CHUNK_BYTES)
        decoder = codecs.getincrementaldecoder(text_encoding(first))()
        chunk = first
        try:
            while chunk:
                if "\x00" in decoder.decode(chunk):
                    return False
                chunk = source.read(_TEXT_CHUNK_BYTES)
            return "\x00" not in decoder.decode(b"", final=True)
        except UnicodeError:
            return False


def detect_file_type(filename: str, path: Path) -> str:
    """Return the content type of a stored upload after checking it is accepted.

    The extension (compared case-insensitively) selects the expected type. The
    file must start with that type's signature bytes, DOCX/XLSX archives must
    contain their main document part, and TXT/Markdown files must be readable
    UTF-8 or BOM-marked UTF-16 text without NUL characters.

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
        raise UnsupportedFileType("Only PDF, XLSX, DOCX, JPEG, PNG, TXT and Markdown files are accepted")
    if expected.text:
        if not _is_readable_text(path):
            raise UnsupportedFileType("The file content does not match its extension")
        return expected.content_type
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
