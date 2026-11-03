"""Offline tests of the OCR API (no GPU, models or vLLM server).

A stub recognizer stands in for the PaddleOCR-VL pipeline and returns page
results shaped like the real ones, so input checks, page order, region order,
Markdown assembly and the HTTP contract can be checked.

    uv run python -m unittest discover -s deployments/ocr/tests -t deployments/ocr -v
"""

from __future__ import annotations

import asyncio
import base64
import threading
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from ocr_server.app import Settings, create_app
from ocr_server.document import (
    IMAGE, PDF, InputError, combine_markdown, page_from_result, sniff,
)

PDF_BYTES = b"%PDF-1.7\n%stub\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\0" * 32


def page_result(page: int, pages: int | None = None) -> tuple[dict, dict]:
    """A pipeline result for one page: a title, a table and a text block."""

    blocks = [
        {"block_label": "paragraph_title", "block_content": f"Title {page}",
         "block_bbox": [10, 10, 200, 40], "block_id": 0, "block_order": 1},
        {"block_label": "table", "block_content": "<table><tr><td>E101</td></tr></table>",
         "block_bbox": [10, 50, 400, 200], "block_id": 1, "block_order": None},
        {"block_label": "text", "block_content": f"Body of page {page}",
         "block_bbox": [10, 210, 400, 240], "block_id": 2, "block_order": 2},
    ]
    result = {"page_index": page - 1 if pages else None, "page_count": pages,
              "width": 1191, "height": 1684, "parsing_res_list": blocks}
    markdown = {"markdown_texts": f"## Title {page}\n\nBody of page {page}",
                "markdown_images": {}}
    return result, markdown


class StubRecognizer:
    ready = True

    def __init__(self, pages: int = 1) -> None:
        self.pages = pages
        self.calls: list[dict] = []
        self.release = threading.Event()
        self.release.set()

    def load(self) -> None:
        pass

    def run(self, path: str, *, orientation: bool, unwarping: bool):
        self.calls.append({"path": path, "orientation": orientation, "unwarping": unwarping})
        self.release.wait(10)
        if path.endswith(".pdf"):
            return [page_result(n, self.pages) for n in range(1, self.pages + 1)]
        return [page_result(1)]


class DocumentTest(unittest.TestCase):
    def test_file_type_comes_from_the_content(self) -> None:
        self.assertEqual((sniff(PDF_BYTES).kind, sniff(PDF_BYTES).suffix), (PDF, ".pdf"))
        self.assertEqual((sniff(PNG_BYTES).kind, sniff(PNG_BYTES).suffix), (IMAGE, ".png"))
        self.assertEqual(sniff(JPEG_BYTES).suffix, ".jpg")
        with self.assertRaises(InputError):
            sniff(b"GIF89a")

    def test_regions_keep_reading_order_and_original_numbering(self) -> None:
        result, markdown = page_result(2, 3)
        page = page_from_result(result, markdown, 2)
        self.assertEqual(page["page_number"], 2)
        self.assertEqual([r["order"] for r in page["regions"]], [1, 2, 3])
        self.assertEqual([r["label"] for r in page["regions"]],
                         ["paragraph_title", "table", "text"])
        self.assertIsNone(page["regions"][1]["block_order"])
        self.assertEqual(page["regions"][2]["bbox"], [10, 210, 400, 240])
        self.assertNotIn("preprocessing", page)
        self.assertNotIn("markdown_images", page)

    def test_preprocessing_and_images_are_reported(self) -> None:
        result, markdown = page_result(1)
        result["doc_preprocessor_res"] = {
            "angle": 270, "model_settings": {"use_doc_orientation_classify": True,
                                             "use_doc_unwarping": False}}
        markdown["markdown_images"] = {"imgs/fig.jpg": Image.new("RGB", (4, 4), "red")}
        page = page_from_result(result, markdown, 1, include_images=True)
        self.assertEqual(page["preprocessing"], {"orientation_angle": 270, "unwarped": False})
        jpeg = base64.b64decode(page["markdown_images"]["imgs/fig.jpg"])
        self.assertTrue(jpeg.startswith(b"\xff\xd8\xff"))

    def test_markdown_is_joined_in_page_order_with_markers(self) -> None:
        pages = [{"page_number": n, "markdown": f"page {n}\n"} for n in (1, 2, 3)]
        text = combine_markdown(pages)
        self.assertEqual(text, "<!-- page 1 -->\n\npage 1\n\n<!-- page 2 -->\n\npage 2\n\n"
                               "<!-- page 3 -->\n\npage 3\n")


class AppTest(unittest.TestCase):
    def client(self, recognizer: StubRecognizer, **settings) -> TestClient:
        settings.setdefault("vlm_url", "http://127.0.0.1:9/v1")
        client = TestClient(create_app(recognizer, Settings(**settings)))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def post(self, client: TestClient, data: bytes, name: str = "doc.pdf", **form):
        return client.post("/v1/ocr", data=form, files={"file": (name, data)})

    @mock.patch("ocr_server.app.pdf_page_count", return_value=3)
    def test_pdf_pages_come_back_in_order_with_markdown(self, _count) -> None:
        recognizer = StubRecognizer(pages=3)
        response = self.post(self.client(recognizer), PDF_BYTES, "manual.bin")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["file_type"], "pdf")
        self.assertEqual(body["page_count"], 3)
        self.assertEqual([p["page_number"] for p in body["pages"]], [1, 2, 3])
        self.assertEqual(body["pages"][2]["regions"][0]["content"], "Title 3")
        self.assertLess(body["markdown"].index("<!-- page 1 -->"),
                        body["markdown"].index("<!-- page 3 -->"))
        self.assertTrue(recognizer.calls[0]["path"].endswith(".pdf"))
        self.assertEqual(recognizer.calls[0]["orientation"], False)

    def test_image_and_markdown_response(self) -> None:
        client = self.client(StubRecognizer())
        response = self.post(client, PNG_BYTES, "scan.png", response_format="markdown")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/markdown"))
        self.assertEqual(response.text, "<!-- page 1 -->\n\n## Title 1\n\nBody of page 1\n")
        body = self.post(client, JPEG_BYTES, "photo.jpeg").json()
        self.assertEqual((body["file_type"], body["page_count"]), ("image", 1))

    def test_preprocessing_needs_the_server_setting(self) -> None:
        refused = self.post(self.client(StubRecognizer()), PNG_BYTES, doc_orientation="true")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("OCR_DOC_PREPROCESSING", refused.json()["detail"])

        recognizer = StubRecognizer()
        client = self.client(recognizer, doc_preprocessing=True)
        response = self.post(client, PNG_BYTES, doc_orientation="true")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["settings"],
                         {"doc_orientation": True, "doc_unwarping": False})
        self.assertEqual((recognizer.calls[0]["orientation"], recognizer.calls[0]["unwarping"]),
                         (True, False))

    def test_bad_inputs_are_refused(self) -> None:
        client = self.client(StubRecognizer(), max_upload_mb=1, max_pages=2)
        self.assertEqual(self.post(client, b"GIF89a...", "a.gif").status_code, 400)
        self.assertEqual(self.post(client, PNG_BYTES, response_format="html").status_code, 400)
        self.assertEqual(self.post(client, PNG_BYTES + b"\0" * 2**20).status_code, 413)
        with mock.patch("ocr_server.app.pdf_page_count", return_value=3):
            self.assertEqual(self.post(client, PDF_BYTES).status_code, 413)

    def test_ready_reports_an_unreachable_vlm_server(self) -> None:
        response = self.client(StubRecognizer()).get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "vlm_unavailable")


class QueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_document_at_a_time_and_a_bounded_queue(self) -> None:
        recognizer = StubRecognizer()
        recognizer.release.clear()
        app = create_app(recognizer, Settings(max_queue=1))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            def post():
                return client.post("/v1/ocr", files={"file": ("a.png", PNG_BYTES)})

            first = asyncio.create_task(post())
            second = asyncio.create_task(post())
            await asyncio.sleep(0.2)
            self.assertEqual(len(recognizer.calls), 1)  # the second one waits
            third = await post()
            self.assertEqual(third.status_code, 503)
            recognizer.release.set()
            self.assertEqual([(await t).status_code for t in (first, second)], [200, 200])
        self.assertEqual(len(recognizer.calls), 2)


if __name__ == "__main__":
    unittest.main()
