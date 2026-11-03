"""OCR API: the PaddleOCR-VL-1.6 pipeline with its VLM served by vLLM.

The full PaddleOCR-VL pipeline runs in this process: PP-DocLayoutV3 finds the
layout regions and their reading order, and each region is sent to the
PaddleOCR-VL-1.6-0.9B model on the vLLM server (`ai-ocr-vlm`) for recognition.
Documents are processed one at a time; a few more may wait (`OCR_MAX_QUEUE`).
Visualizations are never rendered.

Endpoints:
- `GET /health` (process up), `GET /ready` (pipeline loaded and vLLM server up),
  `GET /v1/models`.
- `POST /v1/ocr`: multipart `file` (PDF, PNG or JPEG). Returns every page in
  order with its regions and Markdown, plus the whole document's Markdown;
  `response_format=markdown` returns only that Markdown.

Document preprocessing (orientation correction and unwarping) needs
`OCR_DOC_PREPROCESSING=1`, which loads two more models; requests then turn it
on with `doc_orientation` / `doc_unwarping`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from .document import (
    PDF, InputError, combine_markdown, page_from_result, pdf_page_count, sniff,
)

logger = logging.getLogger(__name__)

PIPELINE = "PaddleOCR-VL-1.6"
VLM_MODEL = "PaddleOCR-VL-1.6-0.9B"


@dataclass(frozen=True)
class Settings:
    vlm_url: str = "http://ai-ocr-vlm:8080/v1"
    device: str = "gpu:0"
    doc_preprocessing: bool = False
    # Layout regions sent to the vLLM server at once.
    vlm_concurrency: int = 8
    # Pages per layout-detection batch. Paddle keeps the GPU memory it has
    # used: on 10 A4 pages, 1 peaked at 0.9 GB and 8 (PaddleOCR's default) at
    # 4.3 GB, for about the same speed.
    layout_batch: int = 1
    max_upload_mb: int = 50
    max_pages: int = 100
    # Documents allowed to wait while one is processed.
    max_queue: int = 4

    @classmethod
    def from_env(cls) -> Settings:
        """Settings from `OCR_*` variables; unset ones keep their defaults."""

        d = cls()
        env = os.environ.get
        return cls(
            vlm_url=env("OCR_VLM_URL") or d.vlm_url,
            device=env("OCR_DEVICE") or d.device,
            doc_preprocessing=env("OCR_DOC_PREPROCESSING") == "1",
            vlm_concurrency=int(env("OCR_VLM_CONCURRENCY") or d.vlm_concurrency),
            layout_batch=int(env("OCR_LAYOUT_BATCH") or d.layout_batch),
            max_upload_mb=int(env("OCR_MAX_UPLOAD_MB") or d.max_upload_mb),
            max_pages=int(env("OCR_MAX_PAGES") or d.max_pages),
            max_queue=int(env("OCR_MAX_QUEUE") or d.max_queue),
        )


class Recognizer(Protocol):
    ready: bool

    def load(self) -> None: ...

    def run(self, path: str, *, orientation: bool, unwarping: bool
            ) -> list[tuple[dict[str, Any], dict[str, Any]]]: ...


class PaddleRecognizer:
    """The PaddleOCR-VL pipeline with the vLLM server as its VLM backend."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ready = False

    def load(self) -> None:
        """Creates the pipeline; the preprocessing models load only if enabled.

        The pipeline starts from PaddleOCR's own `PaddleOCR-VL-1.6` config;
        only the layout batch size is changed there.
        """

        from paddleocr import PaddleOCRVL
        from paddlex.inference import load_pipeline_config

        config = load_pipeline_config(PIPELINE)
        config["SubModules"]["LayoutDetection"]["batch_size"] = self.settings.layout_batch
        self._pipeline = PaddleOCRVL(
            paddlex_config=config,
            vl_rec_backend="vllm-server",
            vl_rec_server_url=self.settings.vlm_url,
            vl_rec_max_concurrency=self.settings.vlm_concurrency,
            use_doc_orientation_classify=self.settings.doc_preprocessing,
            use_doc_unwarping=self.settings.doc_preprocessing,
            device=self.settings.device,
        )
        self.ready = True
        logger.info("%s pipeline loaded on %s (VLM at %s, preprocessing %s)", PIPELINE,
                    self.settings.device, self.settings.vlm_url,
                    "available" if self.settings.doc_preprocessing else "off")

    def run(self, path: str, *, orientation: bool, unwarping: bool
            ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Page results in page order, as (`json["res"]`, `markdown`) pairs."""

        options: dict[str, Any] = {}
        if self.settings.doc_preprocessing:
            options = {"use_doc_orientation_classify": orientation,
                       "use_doc_unwarping": unwarping}
        return [(result.json["res"], result.markdown)
                for result in self._pipeline.predict(path, **options)]


def create_app(recognizer: Recognizer | None = None, settings: Settings | None = None) -> FastAPI:
    """Builds the app; tests pass a stub recognizer that is already loaded."""

    settings = settings or Settings.from_env()
    recognizer = recognizer or PaddleRecognizer(settings)
    one_at_a_time = asyncio.Semaphore(1)
    waiting = 0

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not recognizer.ready:
            await asyncio.to_thread(recognizer.load)
        yield

    app = FastAPI(title="PaddleOCR-VL OCR API", lifespan=lifespan)

    async def vlm_up() -> bool:
        base = settings.vlm_url.rstrip("/").removesuffix("/v1")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                return (await client.get(f"{base}/health")).status_code == 200
        except httpx.HTTPError:
            return False

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        status = {"pipeline": PIPELINE, "vlm_model": VLM_MODEL,
                  "doc_preprocessing": settings.doc_preprocessing}
        if not recognizer.ready:
            return JSONResponse({"status": "loading", **status}, status_code=503)
        if not await vlm_up():
            return JSONResponse({"status": "vlm_unavailable", **status}, status_code=503)
        return {"status": "ready", **status}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{
            "id": VLM_MODEL, "object": "model", "owned_by": "PaddlePaddle",
            "pipeline": PIPELINE,
            "capabilities": ["ocr", "layout_regions", "reading_order", "markdown",
                             *(["doc_preprocessing"] if settings.doc_preprocessing else [])],
        }]}

    @app.post("/v1/ocr", summary="OCR a PDF or image", description=OCR_DOC,
              responses=OCR_RESPONSES)
    async def ocr(
        file: UploadFile = File(..., description=(
            f"PDF (up to {settings.max_pages} pages), PNG or JPEG, up to "
            f"{settings.max_upload_mb} MB; the type is read from the content.")),
        response_format: str = Form("json", description=(
            "`json`: pages, layout regions in reading order and Markdown; `markdown`: "
            "the document's Markdown only."), json_schema_extra={"enum": ["json", "markdown"]}),
        doc_orientation: bool = Form(False, description=(
            "Turn rotated pages (90/180/270°) upright. Needs OCR_DOC_PREPROCESSING=1 "
            f"(currently {'on' if settings.doc_preprocessing else 'off'}), else 400.")),
        doc_unwarping: bool = Form(False, description=(
            "Flatten curved or skewed pages (UVDoc). Same condition as `doc_orientation`.")),
        include_images: bool = Form(False, description=(
            "Add the figure crops the Markdown links to, as base64 JPEG "
            "(`pages[].markdown_images`).")),
    ):
        nonlocal waiting
        if response_format not in ("json", "markdown"):
            raise HTTPException(400, "response_format must be json or markdown")
        if (doc_orientation or doc_unwarping) and not settings.doc_preprocessing:
            raise HTTPException(
                400, "document preprocessing is off; start the service with "
                     "OCR_DOC_PREPROCESSING=1 to use doc_orientation or doc_unwarping")
        data = await file.read()
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"file larger than {settings.max_upload_mb} MB")
        try:
            upload = sniff(data)
            pages_in = pdf_page_count(data) if upload.kind == PDF else 1
        except InputError as exc:
            raise HTTPException(400, str(exc)) from exc
        if pages_in > settings.max_pages:
            raise HTTPException(413, f"more than {settings.max_pages} pages ({pages_in})")
        if waiting >= settings.max_queue + 1:
            raise HTTPException(503, "busy: too many documents waiting; retry later")

        waiting += 1
        try:
            async with one_at_a_time:
                started = time.perf_counter()
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / f"input{upload.suffix}"
                    path.write_bytes(data)
                    try:
                        results = await asyncio.to_thread(
                            recognizer.run, str(path),
                            orientation=doc_orientation, unwarping=doc_unwarping)
                    except Exception as exc:
                        logger.exception("OCR failed for %s", file.filename)
                        raise HTTPException(
                            502 if not await vlm_up() else 500,
                            f"OCR failed: {type(exc).__name__}: {exc}") from exc
                elapsed = time.perf_counter() - started
        finally:
            waiting -= 1

        pages = [page_from_result(result, markdown, number, include_images=include_images)
                 for number, (result, markdown) in enumerate(results, start=1)]
        markdown = combine_markdown(pages)
        if response_format == "markdown":
            return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")
        return {
            "pipeline": PIPELINE, "vlm_model": VLM_MODEL,
            "file_name": file.filename, "file_type": upload.kind,
            "page_count": len(pages),
            "settings": {"doc_orientation": doc_orientation, "doc_unwarping": doc_unwarping},
            "elapsed_s": round(elapsed, 2),
            "pages": pages,
            "markdown": markdown,
        }

    return app


OCR_DOC = """\
Runs the PaddleOCR-VL-1.6 pipeline: PP-DocLayoutV3 finds each page's layout
regions and reading order, then PaddleOCR-VL-1.6-0.9B reads each region.
Tables come back as HTML and formulas as LaTeX. Pages are returned in document
order; `markdown` joins them with `<!-- page N -->` markers. One document is
processed at a time and a few more may wait (`OCR_MAX_QUEUE`).
"""

OCR_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"description": "The OCR result.", "content": {
        "application/json": {"example": {
            "pipeline": PIPELINE, "vlm_model": VLM_MODEL, "file_name": "manual.pdf",
            "file_type": "pdf", "page_count": 1,
            "settings": {"doc_orientation": False, "doc_unwarping": False},
            "elapsed_s": 1.25,
            "pages": [{
                "page_number": 1, "width": 1191, "height": 1684,
                "regions": [{
                    "order": 1, "label": "paragraph_title", "bbox": [91, 321, 365, 365],
                    "polygon": [[91.0, 321.0], [365.0, 321.0], [365.0, 365.0], [91.0, 365.0]],
                    "content": "1. Bảng mã lỗi", "block_id": 2, "block_order": 1}],
                "markdown": "### 1. Bảng mã lỗi",
            }],
            "markdown": "<!-- page 1 -->\n\n### 1. Bảng mã lỗi",
        }},
        "text/markdown": {"example": "<!-- page 1 -->\n\n### 1. Bảng mã lỗi"},
    }},
    400: {"description": "Not a PDF/PNG/JPEG, unreadable PDF, unknown `response_format`, "
                         "or preprocessing requested while it is off."},
    413: {"description": "File or page count over the limit (`OCR_MAX_UPLOAD_MB`, "
                         "`OCR_MAX_PAGES`)."},
    500: {"description": "The OCR pipeline failed."},
    502: {"description": "The OCR model server (vLLM) is down."},
    503: {"description": "Too many documents waiting; retry later."},
}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.getenv("OCR_LOG_LEVEL", "INFO").upper())
    # One line per readiness check is noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(create_app(), host=os.getenv("OCR_HOST", "0.0.0.0"),
                port=int(os.getenv("OCR_PORT", "8080")))


if __name__ == "__main__":
    main()
