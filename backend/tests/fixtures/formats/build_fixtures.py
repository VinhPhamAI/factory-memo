"""Rebuild the small, synthetic format fixtures used by converter tests."""

from __future__ import annotations

from pathlib import Path

import pymupdf
from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw


ROOT = Path(__file__).parent


def image_fixture() -> Image.Image:
    """Return a simple synthetic Vietnamese maintenance label."""

    image = Image.new("RGB", (900, 500), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((40, 40, 860, 460), outline="black", width=4)
    drawing.text((80, 100), "MA LOI E101", fill="black")
    drawing.text((80, 190), "Kiem tra cam bien nhiet", fill="black")
    drawing.text((80, 280), "Khong reset truoc khi kiem tra", fill="black")
    drawing.rectangle((70, 350, 830, 430), outline="black", width=2)
    drawing.line((250, 350, 250, 430), fill="black", width=2)
    drawing.text((90, 375), "E101", fill="black")
    drawing.text((280, 375), "Qua nhiet", fill="black")
    return image


def build() -> None:
    """Write all binary fixtures beside this script."""

    ROOT.mkdir(parents=True, exist_ok=True)
    label = image_fixture()
    label.save(ROOT / "label.png", optimize=True)

    manual = pymupdf.open()
    first = manual.new_page()
    first.insert_text((72, 72), "HUONG DAN XU LY LOI TRUC Z")
    first.insert_text((72, 110), "Ma loi E101: kiem tra cam bien nhiet.")
    first.insert_text((72, 150), "Ma loi | Mo ta | Kiem tra")
    first.insert_text((72, 170), "E101 | Qua nhiet | Cam bien nhiet")
    second = manual.new_page()
    second.insert_text((72, 72), "Khong reset truoc khi kiem tra nguon dien.")
    manual.save(ROOT / "manual.pdf", garbage=4, deflate=True)
    manual.close()

    scan = pymupdf.open()
    page = scan.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=(ROOT / "label.png").read_bytes())
    scan.save(ROOT / "scanned.pdf", garbage=4, deflate=True)
    scan.close()

    document = Document()
    document.add_heading("Hướng dẫn xử lý lỗi trục Z", level=1)
    document.add_paragraph("Kiểm tra cảm biến trước khi reset máy.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Mã lỗi"
    table.cell(0, 1).text = "Mô tả"
    table.cell(1, 0).text = "E101"
    table.cell(1, 1).text = "Quá nhiệt"
    document.save(ROOT / "manual.docx")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Mã lỗi"
    sheet.append(["Mã", "Mô tả", "Kiểm tra"])
    sheet.append(["E101", "Quá nhiệt", "Cảm biến nhiệt"])
    sheet.append(["E202", "Mất nguồn", "Cầu chì"])
    workbook.save(ROOT / "fault_codes.xlsx")
    workbook.close()


if __name__ == "__main__":
    build()
