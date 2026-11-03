"""Input checks and conversion of PaddleOCR-VL page results to the API's JSON.

The pipeline returns one result per page, in page order. Each result's
`parsing_res_list` lists the layout regions in reading order; this module
keeps that order, numbers the regions from 1 and adds the page number.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any

PDF = "pdf"
IMAGE = "image"
# File signatures of the accepted formats, and the suffix the pipeline needs to
# recognize each one.
_SIGNATURES = (
    (b"%PDF-", PDF, ".pdf"),
    (b"\x89PNG\r\n\x1a\n", IMAGE, ".png"),
    (b"\xff\xd8\xff", IMAGE, ".jpg"),
)


class InputError(ValueError):
    """The uploaded file cannot be processed."""


@dataclass(frozen=True)
class Upload:
    kind: str
    suffix: str


def sniff(data: bytes) -> Upload:
    """The file type from its first bytes, whatever its name says.

    Raises:
        InputError: The file is not a PDF, PNG or JPEG.
    """

    for signature, kind, suffix in _SIGNATURES:
        if data.startswith(signature):
            return Upload(kind, suffix)
    raise InputError("only PDF, PNG and JPEG files are supported")


def pdf_page_count(data: bytes) -> int:
    """Number of pages of a PDF.

    Raises:
        InputError: The PDF cannot be opened (damaged or encrypted).
    """

    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError as exc:
        raise InputError(f"unreadable PDF: {exc}") from exc
    try:
        return len(document)
    finally:
        document.close()


def page_from_result(result: dict[str, Any], markdown: dict[str, Any], page_number: int,
                     *, include_images: bool = False) -> dict[str, Any]:
    """One page of the response from a pipeline result.

    Args:
        result: The result's `json["res"]`.
        markdown: The result's `markdown` (`markdown_texts`, `markdown_images`).
        page_number: 1-based page number in the document.
        include_images: Whether to add the cropped figures the Markdown links
            to, as base64 JPEG keyed by their path in the Markdown.

    Returns:
        The page size, the regions in reading order and the page's Markdown.
        `order` is the region's position in reading order; `block_order` is
        PaddleOCR's own numbering, which skips tables, images and some other
        labels (null for them).
    """

    regions = [
        {
            "order": index,
            "label": block.get("block_label"),
            "bbox": block.get("block_bbox"),
            "polygon": block.get("block_polygon_points"),
            "content": block.get("block_content", ""),
            "block_id": block.get("block_id"),
            "block_order": block.get("block_order"),
        }
        for index, block in enumerate(result.get("parsing_res_list") or [], start=1)
    ]
    page: dict[str, Any] = {
        "page_number": page_number,
        "width": result.get("width"),
        "height": result.get("height"),
        "regions": regions,
        "markdown": markdown.get("markdown_texts", ""),
    }
    preprocessing = result.get("doc_preprocessor_res")
    if preprocessing:
        settings = preprocessing.get("model_settings") or {}
        page["preprocessing"] = {
            "orientation_angle": preprocessing.get("angle"),
            "unwarped": bool(settings.get("use_doc_unwarping")),
        }
    if include_images:
        page["markdown_images"] = {
            path: _jpeg_base64(image)
            for path, image in (markdown.get("markdown_images") or {}).items()
        }
    return page


def combine_markdown(pages: list[dict[str, Any]]) -> str:
    """The pages' Markdown in page order, each after a `<!-- page N -->` marker."""

    return "\n\n".join(
        f"<!-- page {page['page_number']} -->\n\n{page['markdown'].strip()}" for page in pages
    ) + "\n"


def _jpeg_base64(image: Any) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")
