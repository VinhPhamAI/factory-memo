"""Client for located OCR through the hosted AI services gateway."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import Conversion, ConverterError, Segment


logger = logging.getLogger(__name__)
OCR_NOT_CONFIGURED = "Chưa cấu hình OCR_API_URL (OCR_API_URL is not configured)."
OCR_UNAVAILABLE = "OCR service unavailable"


def warn_ocr_configuration() -> None:
    """Log safe startup diagnostics for the convert worker's OCR connection."""

    base_url = os.getenv("OCR_API_URL", "").strip()
    if not base_url:
        logger.warning(
            "OCR_API_URL is not configured; scanned PDFs and images will fail conversion"
        )
    elif _is_insecure_remote(base_url):
        logger.warning(
            "OCR_API_URL is remote and does not use HTTPS; original files may be exposed"
        )


class OcrConverter:
    """Send one image or scanned PDF to the configured OCR gateway."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
        doc_orientation: bool | None = None,
        max_pages: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        configured_url = base_url if base_url is not None else os.getenv("OCR_API_URL", "")
        self.base_url = configured_url.strip()
        self.api_key = api_key if api_key is not None else os.getenv("OCR_API_KEY", "")
        self.timeout_s = timeout_s or float(os.getenv("OCR_TIMEOUT_S", "") or 300)
        self.doc_orientation = (
            doc_orientation
            if doc_orientation is not None
            else os.getenv("OCR_DOC_ORIENTATION", "0").strip().lower()
            in {"1", "true", "yes"}
        )
        self.max_pages = max_pages or int(os.getenv("OCR_MAX_PAGES", "") or 100)
        self.transport = transport

    async def convert(self, path: Path, content_type: str) -> Conversion:
        """OCR a document and preserve pages, reading order and exact region boxes.

        Raises:
            ConverterError: Configuration, input or service failures occur. Retry is
                enabled only for connection failures, timeouts and server errors.
        """

        if not self.base_url:
            raise ConverterError(OCR_NOT_CONFIGURED)
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConverterError("OCR_API_URL is invalid")
        with path.open("rb") as stream:
            is_pdf = stream.read(5) == b"%PDF"
        page_count = _pdf_page_count(path) if is_pdf else 1
        if page_count > self.max_pages:
            raise ConverterError(f"more than {self.max_pages} pages ({page_count})")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = {
            "response_format": "json",
            "doc_orientation": "true" if self.doc_orientation else "false",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, transport=self.transport, headers=headers
            ) as client:
                with path.open("rb") as stream:
                    response = await client.post(
                        f"{self.base_url.rstrip('/')}/v1/ocr",
                        data=data,
                        files={"file": (path.name, stream, content_type)},
                    )
        except httpx.TransportError as exc:
            raise ConverterError(OCR_UNAVAILABLE, retry=True) from exc
        if response.status_code == 503 or response.status_code >= 500:
            raise ConverterError(OCR_UNAVAILABLE, retry=True)
        if response.status_code >= 400:
            raise ConverterError(_service_message(response))
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConverterError("OCR service returned invalid JSON", retry=True) from exc
        return _conversion(payload)


def _conversion(payload: dict[str, object]) -> Conversion:
    """Map the OCR response to text and located segments without guessing boxes."""

    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ConverterError("OCR service returned invalid JSON", retry=True)
    pieces: list[str] = []
    segments: list[Segment] = []
    position = 0

    def append(value: str) -> tuple[int, int]:
        nonlocal position
        pieces.append(value)
        start = position
        position += len(value)
        return start, position

    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or page_index)
        if pieces:
            append("\n\n")
        append(f"<!-- page {page_number} -->\n\n")
        page_start = position
        markdown = str(page.get("markdown") or "").strip()
        regions = page.get("regions") if isinstance(page.get("regions"), list) else []
        if not markdown:
            markdown = "\n\n".join(
                str(region.get("content") or "").strip()
                for region in regions
                if isinstance(region, dict) and str(region.get("content") or "").strip()
            )
        append(markdown)
        cursor = 0
        mapped = 0
        for region in regions:
            if not isinstance(region, dict):
                continue
            content = str(region.get("content") or "").strip()
            if not content:
                continue
            relative = markdown.find(content, cursor)
            if relative < 0:
                relative = markdown.find(content)
            if relative < 0:
                continue
            cursor = relative + len(content)
            label = str(region.get("label") or "")
            location: dict[str, object] = {
                "type": "image" if label == "image" else "page",
                "page": page_number,
            }
            if region.get("bbox") is not None:
                location["bbox"] = region["bbox"]
            if label:
                location["label"] = label
            if page.get("width") is not None:
                location["page_width"] = page["width"]
            if page.get("height") is not None:
                location["page_height"] = page["height"]
            segments.append(Segment(
                page_start + relative,
                page_start + relative + len(content),
                location,
            ))
            mapped += 1
        if markdown and not mapped:
            segments.append(Segment(
                page_start,
                page_start + len(markdown),
                {"type": "page", "page": page_number},
            ))
    text = "".join(pieces)
    if not text.strip():
        raise ConverterError("OCR không tìm thấy văn bản trong file.")
    details = {
        key: payload[key]
        for key in ("vlm_model", "pipeline", "page_count", "elapsed_s")
        if key in payload
    }
    return Conversion(text=text, segments=segments, converter="ocr", details=details)


def _pdf_page_count(path: Path) -> int:
    """Read the scanned PDF page count before sending original bytes."""

    import pymupdf

    try:
        with pymupdf.open(path) as document:
            return document.page_count
    except Exception as exc:
        raise ConverterError("PDF không đọc được; không thể OCR.") from exc


def _service_message(response: httpx.Response) -> str:
    """Return the provider's safe input-limit detail for a final failure."""

    try:
        body = response.json()
    except ValueError:
        return f"OCR request rejected (HTTP {response.status_code})"
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail else f"OCR request rejected (HTTP {response.status_code})"


def _is_insecure_remote(url: str) -> bool:
    """Return whether an OCR URL sends originals remotely without HTTPS."""

    parsed = urlparse(url)
    return parsed.scheme != "https" and (parsed.hostname or "").lower() not in {
        "localhost", "127.0.0.1", "::1",
    }
