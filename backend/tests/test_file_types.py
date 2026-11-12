"""Offline checks of the file library's accepted types; no database or network."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from backend.src.services.file_types import (
    DOCX_CONTENT_TYPE,
    XLSX_CONTENT_TYPE,
    UnsupportedFileType,
    detect_file_type,
)


def zip_bytes(*members: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for member in members:
            archive.writestr(member, "<xml/>")
    return buffer.getvalue()


PDF = b"%PDF-1.7\n%test\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
DOCX = zip_bytes("[Content_Types].xml", "word/document.xml")
XLSX = zip_bytes("[Content_Types].xml", "xl/workbook.xml")


class DetectFileTypeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def detect(self, filename: str, content: bytes) -> str:
        path = Path(self.tmp.name) / "content"
        path.write_bytes(content)
        return detect_file_type(filename, path)

    def test_accepted_types_get_server_chosen_content_type(self) -> None:
        cases = [
            ("manual.pdf", PDF, "application/pdf"),
            ("photo.png", PNG, "image/png"),
            ("photo.jpg", JPEG, "image/jpeg"),
            ("photo.jpeg", JPEG, "image/jpeg"),
            ("report.docx", DOCX, DOCX_CONTENT_TYPE),
            ("sheet.xlsx", XLSX, XLSX_CONTENT_TYPE),
            ("notes.txt", "Bước 1. Ngắt nguồn.".encode(), "text/plain"),
            ("README.md", b"# Title\n\n- item", "text/markdown"),
        ]
        for filename, content, expected in cases:
            with self.subTest(filename=filename):
                self.assertEqual(self.detect(filename, content), expected)

    def test_extension_is_case_insensitive(self) -> None:
        self.assertEqual(self.detect("MANUAL.PDF", PDF), "application/pdf")
        self.assertEqual(self.detect("Ảnh chụp.JPeG", JPEG), "image/jpeg")

    def test_content_must_match_extension(self) -> None:
        cases = [
            ("renamed-program.pdf", b"MZ\x90\x00 executable"),
            ("photo.png", JPEG),
            ("photo.jpg", PNG),
            ("archive.docx", zip_bytes("readme.txt")),
            ("sheet.docx", XLSX),
            ("report.xlsx", DOCX),
            ("broken.docx", b"PK\x03\x04 not really a zip"),
            ("empty-ish.pdf", b"%PD"),
            ("binary.txt", b"MZ\x90\x00 executable"),
            ("nul.md", b"text\x00more"),
            ("latin1.txt", "café".encode("latin-1")),
            ("truncated.txt", "é".encode()[:1]),
        ]
        for filename, content in cases:
            with self.subTest(filename=filename), self.assertRaises(UnsupportedFileType):
                self.detect(filename, content)

    def test_text_encodings_match_the_text_converter(self) -> None:
        cases = [
            ("bom.txt", b"\xef\xbb\xbfhello"),
            ("utf16.txt", "Bước 1".encode("utf-16")),
            ("utf16be.md", b"\xfe\xff" + "ok".encode("utf-16-be")),
        ]
        for filename, content in cases:
            with self.subTest(filename=filename):
                self.assertIn(self.detect(filename, content), {"text/plain", "text/markdown"})

    def test_multibyte_character_across_a_read_chunk_is_not_damage(self) -> None:
        content = ("a" * 7 + "ế" * 5).encode()
        with mock.patch("backend.src.services.file_types._TEXT_CHUNK_BYTES", 8):
            self.assertEqual(self.detect("chunk.txt", content), "text/plain")

    def test_damaged_archives_are_unsupported_not_server_errors(self) -> None:
        errors = [
            NotImplementedError("compression method"),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            EOFError(),
            zipfile.BadZipFile("bad"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    mock.patch("backend.src.services.file_types.zipfile.ZipFile", side_effect=error), \
                    self.assertRaises(UnsupportedFileType):
                self.detect("report.docx", DOCX)

    def test_unsupported_extensions_are_rejected(self) -> None:
        for filename in ["notes.csv", "program.exe", "slides.pptx", "no-extension", "manual.pdf.exe", ".pdf"]:
            with self.subTest(filename=filename), self.assertRaises(UnsupportedFileType):
                self.detect(filename, PDF)


if __name__ == "__main__":
    unittest.main()
