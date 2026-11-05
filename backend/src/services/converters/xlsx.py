"""Located text extraction from XLSX workbooks."""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import Conversion, ConverterError, Segment


class XlsxConverter:
    """Render non-empty worksheet rows as tab-separated text in read-only mode."""

    async def convert(self, path: Path, content_type: str) -> Conversion:
        """Return worksheet text with one precise cell range per rendered row."""

        del content_type
        return await asyncio.to_thread(self._convert, path)

    def _convert(self, path: Path) -> Conversion:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        source = path.open("rb")
        try:
            workbook = load_workbook(source, read_only=True, data_only=True)
        except Exception as exc:
            source.close()
            raise ConverterError(
                "XLSX không đọc được; không thể chuyển đổi."
            ) from exc
        pieces: list[str] = []
        segments: list[Segment] = []
        position = 0
        rendered_sheets = 0

        def append(value: str) -> tuple[int, int]:
            nonlocal position
            pieces.append(value)
            start = position
            position += len(value)
            return start, position

        try:
            for sheet in workbook.worksheets:
                rows: list[tuple[int, int, int, str]] = []
                for row_number, row in enumerate(
                    sheet.iter_rows(values_only=True), start=1
                ):
                    populated = [
                        index
                        for index, value in enumerate(row, start=1)
                        if value is not None
                    ]
                    if not populated:
                        continue
                    first, last = min(populated), max(populated)
                    values = [
                        "" if value is None else str(value)
                        for value in row[first - 1:last]
                    ]
                    rows.append((row_number, first, last, "\t".join(values)))
                if not rows:
                    continue
                if pieces:
                    append("\n\n")
                append(f"## {sheet.title}\n")
                rendered_sheets += 1
                for index, (row_number, first, last, text) in enumerate(rows):
                    if index:
                        append("\n")
                    start, end = append(text)
                    segments.append(Segment(start, end, {
                        "type": "cells",
                        "sheet": sheet.title,
                        "range": (
                            f"{get_column_letter(first)}{row_number}:"
                            f"{get_column_letter(last)}{row_number}"
                        ),
                    }))
        finally:
            workbook.close()
            source.close()
        text = "".join(pieces)
        if not text.strip():
            raise ConverterError("XLSX không có ô dữ liệu đọc được.")
        return Conversion(
            text=text,
            segments=segments,
            converter="xlsx",
            details={"sheet_count": rendered_sheets},
        )
