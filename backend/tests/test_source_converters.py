"""Offline format, OCR and located-evidence checks for source conversion."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.api.knowledge_management import _render_pdf_page
from backend.src.agents.rag import Chunk
from backend.src.services.application_source_pipeline import ApplicationKnowledgeStore
from backend.src.services.converters import Segment, convert_file, detect_format
from backend.src.services.converters.ocr import (
    OCR_NOT_CONFIGURED,
    OCR_UNAVAILABLE,
    OcrConverter,
)
from backend.src.services.converters.pdf import PdfTextConverter
from backend.src.services.located_evidence import (
    references_for_chunks,
    references_for_record,
)
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage.models import KnowledgeRecord


FIXTURES = Path(__file__).parent / "fixtures" / "formats"


class FormatConverterTest(unittest.IsolatedAsyncioTestCase):
    """Each local converter returns text plus its most precise source locations."""

    async def test_pdf_text_uses_pages_and_block_boxes(self) -> None:
        path = FIXTURES / "manual.pdf"
        with patch.dict("os.environ", {"PDF_MIN_TEXT_CHARS_PER_PAGE": "1"}):
            self.assertEqual(detect_format(path, "application/octet-stream"), "pdf")
            result = await convert_file(path, "application/octet-stream")

        self.assertEqual(result.converter, "pdf_text")
        self.assertIn("<!-- page 1 -->", result.text)
        self.assertIn("HUONG DAN", result.text)
        self.assertEqual(result.segments[0].location["type"], "page")
        self.assertEqual(result.segments[0].location["page"], 1)
        self.assertEqual(len(result.segments[0].location["bbox"]), 4)

    async def test_pdf_evidence_page_renders_for_bbox_overlay(self) -> None:
        path = FIXTURES / "manual.pdf"
        rendered = _render_pdf_page(path, 1)
        with self.assertRaisesRegex(ValueError, "only 2 pages"):
            _render_pdf_page(path, 3)

        self.assertTrue(rendered.startswith(b"\x89PNG\r\n\x1a\n"))

    async def test_scanned_pdf_has_no_usable_text_layer(self) -> None:
        with patch.dict("os.environ", {"PDF_MIN_TEXT_CHARS_PER_PAGE": "1"}):
            self.assertFalse(
                await PdfTextConverter().has_text_layer(FIXTURES / "scanned.pdf")
            )

    async def test_docx_preserves_paragraph_and_table_cell_locations(self) -> None:
        result = await convert_file(FIXTURES / "manual.docx", "application/octet-stream")

        self.assertEqual(result.converter, "docx")
        self.assertIn("E101", result.text)
        self.assertEqual(result.segments[0].location, {"type": "paragraph", "index": 1})
        cell = next(segment for segment in result.segments if segment.location.get("cell"))
        self.assertEqual(cell.location["cell"], "A1")

    async def test_xlsx_uses_sheet_heading_and_row_cell_ranges(self) -> None:
        result = await convert_file(
            FIXTURES / "fault_codes.xlsx", "application/octet-stream"
        )

        self.assertEqual(result.converter, "xlsx")
        self.assertIn("## Mã lỗi", result.text)
        self.assertEqual(result.segments[1].location, {
            "type": "cells", "sheet": "Mã lỗi", "range": "A2:C2",
        })


class OcrConverterTest(unittest.IsolatedAsyncioTestCase):
    """The OCR client preserves location data and classifies provider failures."""

    async def test_success_sends_auth_and_keeps_page_region_order(self) -> None:
        requests: list[httpx.Request] = []
        payload = json.loads((FIXTURES / "ocr_response.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=payload)

        result = await OcrConverter(
            base_url="https://ocr.example.test",
            api_key="secret",
            transport=httpx.MockTransport(handler),
        ).convert(FIXTURES / "label.png", "image/png")

        self.assertEqual(str(requests[0].url), "https://ocr.example.test/v1/ocr")
        self.assertEqual(requests[0].headers["authorization"], "Bearer secret")
        self.assertEqual(result.converter, "ocr")
        self.assertEqual([segment.location["bbox"] for segment in result.segments], [
            [80, 100, 300, 130], [80, 190, 420, 220],
        ])
        self.assertEqual(result.details["vlm_model"], "fixture-vlm")

        await OcrConverter(
            base_url="https://ocr.example.test",
            api_key="",
            transport=httpx.MockTransport(handler),
        ).convert(FIXTURES / "label.png", "image/png")
        self.assertNotIn("authorization", requests[-1].headers)

    async def test_unset_url_and_input_limit_are_final_but_503_retries(self) -> None:
        path = FIXTURES / "label.png"
        with self.assertRaisesRegex(Exception, re.escape(OCR_NOT_CONFIGURED)) as unset:
            await OcrConverter(base_url="").convert(path, "image/png")
        self.assertFalse(unset.exception.retry)

        for status, message, retry in (
            (503, OCR_UNAVAILABLE, True),
            (413, "file larger than 50 MB", False),
        ):
            transport = httpx.MockTransport(
                lambda _request, status=status: httpx.Response(
                    status, json={"detail": "file larger than 50 MB"}
                )
            )
            with self.assertRaisesRegex(Exception, re.escape(message)) as raised:
                await OcrConverter(
                    base_url="http://127.0.0.1:8080", transport=transport
                ).convert(path, "image/png")
            self.assertEqual(raised.exception.retry, retry)

        def unavailable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        with self.assertRaisesRegex(Exception, OCR_UNAVAILABLE) as connection:
            await OcrConverter(
                base_url="http://127.0.0.1:8080",
                transport=httpx.MockTransport(unavailable),
            ).convert(path, "image/png")
        self.assertTrue(connection.exception.retry)


class LocatedEvidenceTest(unittest.TestCase):
    """Exact quotes and passages use locations without inferred coordinates."""

    def test_quote_maps_to_page_and_missing_quote_falls_back_to_source(self) -> None:
        text = "Mã lỗi E101. Kiểm tra cảm biến."
        segments = [
            Segment(0, 12, {"type": "page", "page": 1, "bbox": [1, 2, 3, 4]}),
            Segment(13, len(text), {"type": "page", "page": 2, "bbox": [5, 6, 7, 8]}),
        ]
        found = KnowledgeRecord(
            subject="E101",
            claim="Kiểm tra cảm biến.",
            type=KnowledgeType.FACT,
            context={"quotes": [{"quote": "Kiểm tra cảm biến."}]},
        )
        missing = KnowledgeRecord(
            subject="E102",
            claim="Thay cầu chì.",
            type=KnowledgeType.FACT,
            context={"quotes": [{"quote": "Thay cầu chì."}]},
        )

        self.assertEqual(references_for_record(found, text, segments)[0]["page"], 2)
        self.assertEqual(references_for_record(found, text, []), [{
            "type": "source", "quote": "Kiểm tra cảm biến.",
        }])
        self.assertEqual(references_for_record(missing, text, segments), [{
            "type": "source", "quote": "Thay cầu chì.", "location_missing": True,
        }])

    def test_passage_crossing_pages_returns_both_page_references(self) -> None:
        text = "Dòng cuối trang một.\n\nDòng đầu trang hai."
        boundary = text.index("Dòng đầu")
        segments = [
            Segment(0, boundary - 2, {"type": "page", "page": 1}),
            Segment(boundary, len(text), {"type": "page", "page": 2}),
        ]
        chunk = Chunk(
            doc_id="manual", index=0,
            text="Dòng cuối trang một. Dòng đầu trang hai.",
        )

        references = references_for_chunks([chunk], text, segments)[0]

        self.assertEqual([reference["page"] for reference in references], [1, 2])


class LocatedStoreTest(unittest.IsolatedAsyncioTestCase):
    """Knowledge ingestion passes per-record locations to the atomic repository call."""

    async def test_record_quote_references_are_stored_per_record(self) -> None:
        captured: dict[str, object] = {}

        class Chunks:
            async def add_records_and_updates(self, *args, **kwargs):
                captured.update(kwargs)
                return [args[2][0].id]

        class Embeddings:
            async def aembed_documents(self, texts):
                return [[1.0] for _text in texts]

        text = "Mã lỗi E101"
        record = KnowledgeRecord(
            subject="E101",
            claim=text,
            type=KnowledgeType.FACT,
            context={"quotes": [{"quote": text}]},
        )
        store = ApplicationKnowledgeStore(
            record.id,
            record.id,
            Chunks(),
            Embeddings(),
            vector_size=1,
            source_text=text,
            segments=(Segment(0, len(text), {"type": "page", "page": 4}),),
        )

        await store.add_knowledge([record])

        references = captured["references_by_record"]
        self.assertEqual(references[record.id][0]["page"], 4)


if __name__ == "__main__":
    unittest.main()
