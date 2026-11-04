"""Focused offline checks for the application-database source pipeline."""

from __future__ import annotations

import tempfile
import unittest
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.src.services.application_source_pipeline import convert_plain_text
from backend.src.services.source_pipeline import NO_CONVERTER, UNREADABLE_TEXT, JobFailed
from backend.src.storage.repositories.sources import FileEntry


class PlainTextConversionTest(unittest.TestCase):
    """The converter preserves strict decoding and its audit metadata contract."""

    def file(self, *, filename: str = "manual.txt", content_type: str = "text/plain") -> FileEntry:
        return FileEntry(
            id=uuid4(),
            filename=filename,
            content_type=content_type,
            size_bytes=10,
            storage_key=f"factory/{uuid4().hex}/content",
            sha256="a" * 64,
            created_at=datetime.now(timezone.utc),
        )

    def test_utf8_text_returns_conversion_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "content"
            path.write_bytes(b"\xef\xbb\xbfPump inspection")
            text, conversion = convert_plain_text(self.file(), path)

        self.assertEqual(text, "Pump inspection")
        self.assertEqual(conversion["encoding"], "utf-8-sig")
        self.assertEqual(len(conversion["sha256"]), 64)
        self.assertIn("+00:00", conversion["at"])

    def test_unsupported_and_unreadable_files_fail_without_replacement_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "content"
            path.write_bytes(b"\xff\x00\xfe")
            with self.assertRaisesRegex(JobFailed, re.escape(NO_CONVERTER)):
                convert_plain_text(self.file(filename="scan.pdf", content_type="application/pdf"), path)
            with self.assertRaisesRegex(JobFailed, re.escape(UNREADABLE_TEXT)):
                convert_plain_text(self.file(), path)


if __name__ == "__main__":
    unittest.main()
