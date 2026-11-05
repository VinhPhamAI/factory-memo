import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KnowledgeManagementPage } from "./knowledge-management";
import type { SearchResponse, SearchResult } from "@/services/knowledge-search";

const { useAuth } = vi.hoisted(() => ({
  useAuth: vi.fn(() => ({
    status: "signed-in" as const,
    user: { id: "1", role: "employee" },
  })),
}));
vi.mock("@/hooks/auth", () => ({ useAuth }));

const result = (overrides: Partial<SearchResult> = {}): SearchResult => ({
  rank: 1,
  chunk_id: "chunk1",
  citation: "[K:chunk1]",
  kind: "knowledge",
  status: "draft",
  title: "Pump P-101",
  content: "Thay phớt trục trước khi chạy lại.",
  scores: { fused: 0.03, dense: 0.8, keyword: 0.4 },
  evidence: [
    {
      source_id: "source1",
      source_title: "manual.pdf",
      source_kind: "document",
      references: [{ type: "page", page: 3, quote: "phớt trục" }],
      original_url: "/v1/knowledge-management/general/sources/upload/source1/original",
    },
  ],
  ...overrides,
});

const answer = (results: SearchResult[]): SearchResponse => ({
  query: "phớt trục",
  mode: "hybrid",
  embedding_model: "model-a",
  results,
});

function stubApi(search: (body: Record<string, unknown>) => Response) {
  const calls: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/knowledge-agent/knowledge-bases"))
        return Response.json({
          knowledge_bases: [{ name: "general", title: "General", description: "" }],
        });
      if (url.endsWith("/knowledge-management/general/search")) {
        expect(init?.method).toBe("POST");
        const body = JSON.parse(init?.body as string);
        calls.push(body);
        return search(body);
      }
      if (url.includes("/sources?"))
        return Response.json({
          sources: [], warnings: [], next_offset: null, qa_mode: "knowledge",
        });
      throw new Error(`Unexpected request ${url}`);
    }),
  );
  return calls;
}

function show() {
  const router = createMemoryRouter(
    [
      { path: "/knowledge", element: <KnowledgeManagementPage /> },
      {
        path: "/knowledge/:kb/sources/:kind/:sourceId",
        element: <p data-testid="source-page">source page</p>,
      },
    ],
    { initialEntries: ["/knowledge?tab=search"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

async function ask(text = "phớt trục") {
  const user = userEvent.setup();
  await screen.findByRole("option", { name: "General" });
  await user.type(screen.getByLabelText("Câu hỏi hoặc từ khóa"), text);
  await user.click(screen.getByRole("button", { name: "Tìm kiếm" }));
  return user;
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("knowledge search view", () => {
  it("opens from the URL and sends the chosen mode and filters", async () => {
    const calls = stubApi(() => Response.json(answer([result()])));
    show();
    const user = userEvent.setup();
    expect(await screen.findByLabelText("Câu hỏi hoặc từ khóa")).toBeInTheDocument();
    await screen.findByRole("option", { name: "General" });
    expect(screen.getByRole("button", { name: "Tìm kiếm" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Chế độ"), "keyword");
    await user.click(screen.getByLabelText("Đoạn tài liệu"));
    await user.click(screen.getByLabelText("Chờ duyệt"));
    await user.type(screen.getByLabelText("Câu hỏi hoặc từ khóa"), "  phớt trục ");
    await user.click(screen.getByRole("button", { name: "Tìm kiếm" }));
    await screen.findByTestId("search-result");
    expect(calls).toEqual([
      {
        query: "phớt trục",
        mode: "keyword",
        kinds: ["knowledge"],
        statuses: ["approved"],
      },
    ]);
  });

  it("shows rank, kind, title and content with drafts distinct from approved records", async () => {
    stubApi(() =>
      Response.json(
        answer([
          result(),
          result({
            rank: 2,
            chunk_id: "chunk2",
            citation: "[D:doc1]",
            kind: "document_passage",
            status: "approved",
            title: "Manual",
            content: "Đoạn tài liệu về phớt.",
            evidence: [],
          }),
        ]),
      ),
    );
    show();
    await ask();
    const [draft, approved] = await screen.findAllByTestId("search-result");
    expect(draft).toHaveAttribute("data-status", "draft");
    expect(approved).toHaveAttribute("data-status", "approved");
    expect(within(draft).getByText("#1")).toBeInTheDocument();
    expect(within(draft).getByText("Knowledge item")).toBeInTheDocument();
    expect(within(draft).getByText("Pump P-101")).toBeInTheDocument();
    expect(within(draft).getByText(/Thay phớt trục/)).toBeInTheDocument();
    expect(within(draft).getByTestId("search-status")).toHaveTextContent(
      "Bản nháp · Chờ duyệt",
    );
    expect(within(approved).getByTestId("search-status")).toHaveTextContent(
      "Đã duyệt",
    );
    expect(within(approved).getByText("Đoạn tài liệu")).toBeInTheDocument();
    expect(within(approved).getByText("[D:doc1]")).toBeInTheDocument();
  });

  it("opens the source of an evidence chip", async () => {
    stubApi(() => Response.json(answer([result()])));
    const router = show();
    const user = await ask();
    const chip = await screen.findByTestId("search-evidence");
    expect(chip).toHaveTextContent("manual.pdf · trang 3");
    await user.click(chip);
    await screen.findByTestId("source-page");
    expect(router.state.location.pathname).toBe(
      "/knowledge/general/sources/upload/source1",
    );
    expect(router.state.location.state).toEqual({
      located: { type: "page", page: 3, quote: "phớt trục" },
    });
  });

  it("opens a source without an original file as a text source", async () => {
    stubApi(() =>
      Response.json(
        answer([
          result({
            evidence: [
              {
                source_id: "note1",
                source_title: "Ghi chú",
                source_kind: "text",
                references: [{ type: "source" }],
                original_url: null,
              },
            ],
          }),
        ]),
      ),
    );
    const router = show();
    const user = await ask();
    await user.click(await screen.findByTestId("search-evidence"));
    await screen.findByTestId("source-page");
    expect(router.state.location.pathname).toBe(
      "/knowledge/general/sources/conversation/note1",
    );
  });

  it("says nothing was found for an empty result", async () => {
    stubApi(() => Response.json(answer([])));
    show();
    await ask();
    expect(await screen.findByText("Không tìm thấy")).toBeInTheDocument();
    expect(screen.queryByTestId("search-result")).toBeNull();
  });

  it("shows the server message when the embedding model does not match", async () => {
    const message =
      "Knowledge base general was embedded with model-z but EMBEDDING_MODEL is model-a. Restore EMBEDDING_MODEL, or re-embed the knowledge base with the new model before searching or ingesting.";
    stubApi(() => Response.json({ detail: message }, { status: 409 }));
    show();
    await ask();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("explains an unavailable service and keeps keyword search possible", async () => {
    stubApi(() => Response.json({ detail: "down" }, { status: 503 }));
    show();
    await ask();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /tạm thời không khả dụng.*từ khóa/,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Tìm kiếm" })).toBeEnabled(),
    );
  });
});
