"""Strict plain-text conversion."""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import Conversion, ConverterError


class TextConverter:
    """Decode an uploaded text file without replacing invalid bytes."""

    async def convert(self, path: Path, content_type: str) -> Conversion:
        """Decode UTF-8 or BOM-marked UTF-16 text.

        Raises:
            ConverterError: The file is empty, binary, or cannot be decoded.
        """

        del content_type
        raw = await asyncio.to_thread(path.read_bytes)
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            text = raw.decode(encoding)
        except UnicodeError as exc:
            raise ConverterError(
                "File không phải văn bản đọc được (UTF-8/UTF-16) hoặc rỗng; "
                "không thể chuyển đổi."
            ) from exc
        if not text.strip() or "\x00" in text:
            raise ConverterError(
                "File không phải văn bản đọc được (UTF-8/UTF-16) hoặc rỗng; "
                "không thể chuyển đổi."
            )
        return Conversion(text=text, segments=[], converter="text", details={"encoding": encoding})

