"""Content-detected converters for uploaded knowledge sources."""

from __future__ import annotations

import os
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


PDF = "pdf"
DOCX = "docx"
XLSX = "xlsx"
IMAGE = "image"
TEXT = "text"


@dataclass(frozen=True)
class Segment:
    """A span of extracted text and its exact location in the original file."""

    start: int
    end: int
    location: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        """Return the JSON-compatible representation stored in source metadata."""

        return asdict(self)


@dataclass(frozen=True)
class Conversion:
    """Text extracted from one file, its locations and converter audit details."""

    text: str
    segments: list[Segment]
    converter: str
    details: dict[str, Any]


class Converter(Protocol):
    """Convert one detected file format to located text."""

    async def convert(self, path: Path, content_type: str) -> Conversion: ...


class ConverterError(Exception):
    """A reviewer-safe conversion failure with an explicit retry decision."""

    def __init__(self, message: str, *, retry: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retry = retry


def detect_format(
    path: Path, content_type: str, filename: str | None = None
) -> str | None:
    """Detect a supported format from bytes before considering the media type.

    Args:
        path: Stored original file.
        content_type: Upload media type, used only for otherwise unmarked text.
        filename: Original name, used only for the established text suffixes.

    Returns:
        A converter format name, or ``None`` when no converter accepts the file.
    """

    with path.open("rb") as stream:
        head = stream.read(8192)
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
        return IMAGE
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = frozenset(archive.namelist())
        except (OSError, zipfile.BadZipFile):
            return None
        if "word/document.xml" in names:
            return DOCX
        if "xl/workbook.xml" in names:
            return XLSX
        return None
    if (
        content_type.lower().startswith("text/")
        or Path(filename or "").suffix.lower() in {
            ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log",
        }
        or _looks_like_text(head)
    ):
        return TEXT
    return None


def can_convert_file(content_type: str, filename: str) -> bool:
    """Return whether upload metadata identifies a format supported by a converter."""

    media_type = content_type.lower().split(";", 1)[0].strip()
    suffix = Path(filename).suffix.lower()
    return media_type.startswith("text/") or media_type in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/png",
        "image/jpeg",
    } or suffix in {
        ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log",
        ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg",
    }


async def convert_file(
    path: Path, content_type: str, filename: str | None = None
) -> Conversion:
    """Select a converter from the stored bytes and extract located text.

    Raises:
        ConverterError: The format is unsupported, invalid, or its provider fails.
    """

    detected = detect_format(path, content_type, filename)
    if detected == TEXT:
        from .text import TextConverter

        return await TextConverter().convert(path, content_type)
    if detected == PDF:
        from .ocr import OcrConverter
        from .pdf import PdfTextConverter

        pdf = PdfTextConverter()
        if await pdf.has_text_layer(path):
            return await pdf.convert(path, content_type)
        return await OcrConverter().convert(path, content_type)
    if detected == DOCX:
        from .docx import DocxConverter

        return await DocxConverter().convert(path, content_type)
    if detected == XLSX:
        from .xlsx import XlsxConverter

        return await XlsxConverter().convert(path, content_type)
    if detected == IMAGE:
        from .ocr import OcrConverter

        return await OcrConverter().convert(path, content_type)
    raise ConverterError(
        "Chưa có OCR/bộ chuyển đổi cho loại file này. File gốc đã được lưu."
    )


def pdf_min_text_chars_per_page() -> int:
    """Return the minimum average PDF text-layer size before OCR is used."""

    raw = os.getenv("PDF_MIN_TEXT_CHARS_PER_PAGE", "").strip()
    return max(0, int(raw)) if raw else 50


def _looks_like_text(head: bytes) -> bool:
    """Accept unmarked UTF text while rejecting binary content with NUL bytes."""

    if not head or b"\x00" in head:
        return False
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            head.decode(encoding)
            return True
        except UnicodeError:
            continue
    return False
