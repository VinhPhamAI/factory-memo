"""Located text extraction from DOCX files."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator

from . import Conversion, ConverterError, Segment


class DocxConverter:
    """Extract paragraphs and table cells from a DOCX in document order."""

    async def convert(self, path: Path, content_type: str) -> Conversion:
        """Return DOCX text with paragraph and table-cell locations."""

        del content_type
        return await asyncio.to_thread(self._convert, path)

    def _convert(self, path: Path) -> Conversion:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        try:
            document = Document(path)
        except Exception as exc:
            raise ConverterError(
                "DOCX không đọc được; không thể chuyển đổi."
            ) from exc

        pieces: list[str] = []
        segments: list[Segment] = []
        position = 0
        paragraph_index = 0
        table_index = 0

        def add(text: str, location: dict[str, Any]) -> None:
            nonlocal position
            text = text.strip()
            if not text:
                return
            if pieces:
                pieces.append("\n\n")
                position += 2
            start = position
            pieces.append(text)
            position += len(text)
            segments.append(Segment(start, position, location))

        for block in _blocks(document):
            if isinstance(block, Paragraph):
                paragraph_index += 1
                add(block.text, {"type": "paragraph", "index": paragraph_index})
                continue
            if isinstance(block, Table):
                table_index += 1
                for row_index, row in enumerate(block.rows, start=1):
                    for column_index, cell in enumerate(row.cells, start=1):
                        paragraph_index += 1
                        add(cell.text, {
                            "type": "paragraph",
                            "index": paragraph_index,
                            "table": table_index,
                            "cell": f"{_column_name(column_index)}{row_index}",
                        })
        text = "".join(pieces)
        if not text.strip():
            raise ConverterError("DOCX không có văn bản đọc được.")
        return Conversion(
            text=text,
            segments=segments,
            converter="docx",
            details={"paragraph_count": paragraph_index, "table_count": table_index},
        )


def _blocks(document: Any) -> Iterator[Any]:
    """Yield top-level paragraphs and tables in their XML order."""

    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _column_name(index: int) -> str:
    """Convert a one-based column index to an Excel-style label."""

    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result
