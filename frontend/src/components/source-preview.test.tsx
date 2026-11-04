import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LocatedEvidencePreview } from "./source-preview";

describe("LocatedEvidencePreview", () => {
  it("opens a PDF at the referenced page", () => {
    render(
      <LocatedEvidencePreview
        reference={{ type: "page", page: 3, bbox: [10, 20, 30, 40] }}
        url="blob:manual"
        contentType="application/pdf"
      />,
    );

    expect(screen.getByTitle("Bằng chứng trang 3")).toHaveAttribute(
      "src",
      "blob:manual#page=3",
    );
    expect(screen.getByText("Vùng: 10, 20, 30, 40")).toBeInTheDocument();
  });

  it("shows sheet cells and explicit missing-location fallback", () => {
    const { rerender } = render(
      <LocatedEvidencePreview
        reference={{
          type: "cells",
          sheet: "Mã lỗi",
          range: "A2:D9",
          quote: "E101",
        }}
      />,
    );
    expect(screen.getByText("Mã lỗi · A2:D9")).toBeInTheDocument();
    expect(screen.getByText("E101")).toBeInTheDocument();

    rerender(
      <LocatedEvidencePreview
        reference={{
          type: "source",
          quote: "Không có trong bản trích xuất",
          location_missing: true,
        }}
      />,
    );
    expect(screen.getByText("Không xác định được vị trí")).toBeInTheDocument();
  });

  it("draws an image bbox using the OCR page dimensions", () => {
    const { container } = render(
      <LocatedEvidencePreview
        reference={{
          type: "image",
          page: 1,
          bbox: [100, 200, 300, 600],
          page_width: 1000,
          page_height: 1000,
        }}
        url="blob:label"
        contentType="image/png"
      />,
    );

    expect(screen.getByAltText("Bằng chứng trong file gốc")).toHaveAttribute(
      "src",
      "blob:label",
    );
    expect(container.querySelector(".located-image-box")).toHaveStyle({
      left: "10%",
      top: "20%",
      width: "20%",
      height: "40%",
    });
  });

  it("draws a PDF bbox over a rendered page image", () => {
    const { container } = render(
      <LocatedEvidencePreview
        reference={{
          type: "page",
          page: 2,
          bbox: [50, 100, 150, 300],
          page_width: 500,
          page_height: 500,
        }}
        url="blob:rendered-page"
        contentType="application/pdf"
        renderedPage
      />,
    );
    expect(screen.getByAltText("Bằng chứng trong file gốc")).toHaveAttribute(
      "src",
      "blob:rendered-page",
    );
    expect(container.querySelector(".located-image-box")).toBeInTheDocument();
  });
});
