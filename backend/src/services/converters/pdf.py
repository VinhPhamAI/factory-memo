"""Located extraction from PDF text layers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from . import Conversion, ConverterError, Segment, pdf_min_text_chars_per_page


class PdfTextConverter:
    """Extract page blocks from a readable PDF text layer with their bounding boxes."""

    async def has_text_layer(self, path: Path) -> bool:
        """Return whether average page text reaches the configured OCR threshold.

        Raises:
            ConverterError: The PDF is unreadable or encrypted.
        """

        return await asyncio.to_thread(self._has_text_layer, path)

    def _has_text_layer(self, path: Path) -> bool:
        import pymupdf

        try:
            with pymupdf.open(path) as document:
                if document.needs_pass:
                    raise ConverterError("PDF được mã hóa; không thể chuyển đổi.")
                page_count = document.page_count
                total = sum(len(page.get_text("text").strip()) for page in document)
        except ConverterError:
            raise
        except Exception as exc:
            raise ConverterError(
                "PDF không đọc được; không thể chuyển đổi."
            ) from exc
        average = total / page_count if page_count else 0
        return average >= pdf_min_text_chars_per_page()

    async def convert(self, path: Path, content_type: str) -> Conversion:
        """Extract ordered text blocks and page bounding boxes from a PDF."""

        del content_type
        return await asyncio.to_thread(self._convert, path)

    def _convert(self, path: Path) -> Conversion:
        import pymupdf

        pieces: list[str] = []
        segments: list[Segment] = []
        position = 0

        def append(value: str) -> tuple[int, int]:
            nonlocal position
            pieces.append(value)
            start = position
            position += len(value)
            return start, position

        try:
            with pymupdf.open(path) as document:
                if document.needs_pass:
                    raise ConverterError("PDF được mã hóa; không thể chuyển đổi.")
                for page_number, page in enumerate(document, start=1):
                    if pieces:
                        append("\n\n")
                    append(f"<!-- page {page_number} -->\n\n")
                    blocks: list[tuple[Any, ...]] = sorted(
                        page.get_text("blocks"),
                        key=lambda block: (block[1], block[0], block[5]),
                    )
                    wrote = False
                    for block in blocks:
                        if len(block) > 6 and block[6] != 0:
                            continue
                        text = str(block[4]).strip()
                        if not text:
                            continue
                        if wrote:
                            append("\n\n")
                        start, end = append(text)
                        segments.append(Segment(start, end, {
                            "type": "page",
                            "page": page_number,
                            "bbox": [round(float(value), 2) for value in block[:4]],
                            "page_width": round(float(page.rect.width), 2),
                            "page_height": round(float(page.rect.height), 2),
                        }))
                        wrote = True
                text = "".join(pieces)
                details = {"page_count": document.page_count}
        except ConverterError:
            raise
        except Exception as exc:
            raise ConverterError(
                "PDF không đọc được; không thể chuyển đổi."
            ) from exc
        if not text.strip():
            raise ConverterError("PDF không có lớp văn bản đọc được.")
        return Conversion(
            text=text,
            segments=segments,
            converter="pdf_text",
            details=details,
        )

