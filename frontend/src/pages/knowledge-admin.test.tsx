import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KnowledgeAdminPage } from "./knowledge-admin";

vi.mock("@/hooks/auth", () => ({
  useAuth: () => ({
    status: "signed-in",
    user: { id: "admin", role: "admin" },
  }),
}));

const knowledgeBases = [
  { name: "denso_kb", title: "Denso KB", description: "", is_default: false },
  {
    name: "maintenance_db",
    title: "Maintenance DB",
    description: "",
    is_default: true,
  },
];

afterEach(() => vi.restoreAllMocks());

describe("knowledge administration", () => {
  it("replaces details and sources without accumulating panels when switching KBs", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/v1/knowledge-bases"))
        return Response.json({ knowledge_bases: knowledgeBases });

      const kb = knowledgeBases.find((item) => url.includes(`/${item.name}`));
      if (!kb) throw new Error(`Unexpected request: ${url}`);
      if (url.includes("/sources?"))
        return Response.json({
          sources: [
            {
              id: "source",
              kb: kb.name,
              kind: "upload",
              name: `${kb.name}.pdf`,
              stage: "raw",
              review: "none",
              processing: {},
            },
          ],
          next_offset: null,
          warnings: [],
          qa_mode: "knowledge",
        });
      return Response.json({
        ...kb,
        template: "general",
        created_by: "admin",
        created_at: "2026-11-01T00:00:00Z",
        updated_at: "2026-11-01T00:00:00Z",
        source_counts: { received: kb.is_default ? 3 : 0 },
        chunk_counts: { knowledge: { draft: kb.is_default ? 126 : 73 } },
      });
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <KnowledgeAdminPage />
      </MemoryRouter>,
    );
    const panel = within(
      screen.getByRole("region", { name: "Kho kiến thức được chọn" }),
    );

    for (const kb of [
      knowledgeBases[1],
      knowledgeBases[0],
      knowledgeBases[1],
      knowledgeBases[0],
      knowledgeBases[1],
    ]) {
      await user.click(
        await screen.findByRole("button", { name: new RegExp(kb.title) }),
      );
      await panel.findByRole("heading", { name: kb.title });
      await panel.findByRole("link", { name: `Xem chi tiết ${kb.name}.pdf` });
      expect(
        panel.getAllByRole("region", { name: "Thông tin kho kiến thức" }),
      ).toHaveLength(1);
      expect(
        panel.getAllByRole("button", { name: "Sửa thông tin" }),
      ).toHaveLength(1);
      expect(panel.getAllByRole("button", { name: "Xóa kho" })).toHaveLength(1);
      if (kb.is_default)
        expect(panel.getByRole("button", { name: "Xóa kho" })).toBeDisabled();
      else expect(panel.getByRole("button", { name: "Xóa kho" })).toBeEnabled();
      expect(panel.getAllByText("Chi tiết kỹ thuật")).toHaveLength(1);
      expect(panel.getAllByTestId("kb-source-list")).toHaveLength(1);
      expect(panel.getAllByTestId("kb-source-item")).toHaveLength(1);
      expect(
        panel.getByText(kb.is_default ? "Nháp 126" : "Nháp 73"),
      ).toBeInTheDocument();
      expect(
        panel.queryByText(
          `${knowledgeBases.find((item) => item.name !== kb.name)!.name}.pdf`,
        ),
      ).not.toBeInTheDocument();
    }
  });
});
