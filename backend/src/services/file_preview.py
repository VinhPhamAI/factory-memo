"""Create bounded previews of files stored in the personal library."""

from pathlib import Path

from backend.src.services.file_types import DOCX_CONTENT_TYPE, XLSX_CONTENT_TYPE


def create_file_preview(path: Path, content_type: str) -> tuple[bytes, str]:
    """Render a first page or a short office-file text excerpt.

    Raises:
        ValueError: The file cannot be previewed or has no readable content.
    """
    try:
        if content_type == DOCX_CONTENT_TYPE:
            from docx import Document

            document = Document(path)
            excerpt = "\n".join(p.text for p in document.paragraphs[:30])[:4000]
            return (excerpt or "Không có văn bản xem trước.").encode(), "text/plain; charset=utf-8"
        if content_type == XLSX_CONTENT_TYPE:
            from openpyxl import load_workbook

            with path.open("rb") as source:
                book = load_workbook(source, read_only=True, data_only=True)
                try:
                    sheet = book.active
                    if sheet is None:
                        raise ValueError("Workbook has no sheets")
                    rows = sheet.iter_rows(max_row=15, max_col=8, values_only=True)
                    excerpt = "\n".join("\t".join("" if value is None else str(value)[:200] for value in row) for row in rows)[:4000]
                    return (excerpt or "Không có dữ liệu xem trước.").encode(), "text/plain; charset=utf-8"
                finally:
                    book.close()
        import pymupdf

        kind = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpeg"}.get(content_type)
        if kind is None:
            raise ValueError("Unsupported preview type")
        with pymupdf.open(filename=str(path), filetype=kind) as document:
            if not document.is_pdf:
                with pymupdf.open("pdf", document.convert_to_pdf()) as pdf:
                    return _first_page(pdf)
            return _first_page(document)
    except Exception as exc:
        raise ValueError("Cannot preview file") from exc


def _first_page(document) -> tuple[bytes, str]:
    """Render the first page to a PNG of at most 480 pixels on its long edge."""
    if not len(document):
        raise ValueError("Document has no pages")
    import pymupdf

    page = document.load_page(0)
    width, height = page.rect.width, page.rect.height
    if not 0 < width < 1_000_000 or not 0 < height < 1_000_000:
        raise ValueError("Invalid page dimensions")
    scale = min(1.0, 480 / max(width, height))
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png"), "image/png"
