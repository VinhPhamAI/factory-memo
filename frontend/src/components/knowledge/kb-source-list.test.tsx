import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KnowledgeBaseSourceList } from "./kb-source-list";

const item = (id: string, name: string) => ({
  id,
  kb: "denso_kb",
  kind: "upload",
  name,
  stage: "extracted",
  review: "pending",
  created_at: null,
  processing: {},
});

function page(sources: unknown[], next_offset: number | null) {
  return Response.json({ sources, next_offset, warnings: [], qa_mode: "knowledge" });
}

afterEach(() => vi.restoreAllMocks());

describe("KnowledgeBaseSourceList", () => {
  it("links each source to its detail page and loads further pages", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) =>
        String(input).includes("offset=0")
          ? page([item("a1", "manual.pdf")], 1)
          : page([item("b2", "guide.docx")], null),
      );
    render(
      <MemoryRouter>
        <KnowledgeBaseSourceList kb="denso_kb" returnTo="/admin/knowledge-bases?kb=denso_kb" />
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("link", { name: "Xem chi tiết manual.pdf" }),
    ).toHaveAttribute("href", "/knowledge/denso_kb/sources/upload/a1");
    expect(screen.getByText(/Chờ duyệt/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Tải thêm/ }));
    expect(
      await screen.findByRole("link", { name: "Xem chi tiết guide.docx" }),
    ).toHaveAttribute("href", "/knowledge/denso_kb/sources/upload/b2");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Tải thêm/ })).toBeNull(),
    );
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("filters by name and reports an empty knowledge base", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      page([item("a1", "manual.pdf")], null),
    );
    render(
      <MemoryRouter>
        <KnowledgeBaseSourceList kb="denso_kb" returnTo="/admin/knowledge-bases?kb=denso_kb" />
      </MemoryRouter>,
    );
    await screen.findByText("manual.pdf");
    await userEvent.type(screen.getByLabelText("Tìm tài liệu trong kho"), "zzz");
    expect(screen.getByText("Không tìm thấy tài liệu phù hợp.")).toBeInTheDocument();
  });
});
