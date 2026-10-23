import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  KnowledgeManagementPage,
  KnowledgeSourcePage,
  LegacyCaptureReviewRedirect,
} from "./knowledge-management";
import type { ManagedSourceDetail } from "@/services/knowledge-management";

const rawText = (): ManagedSourceDetail => ({
  id: "upload1",
  kb: "general",
  kind: "upload",
  name: "notes.txt",
  content_type: "text/plain",
  created_at: "2026-10-23T00:00:00Z",
  stage: "raw",
  mode: null,
  review: "none",
  counts: {},
  processing: {},
  can_convert: true,
  can_extract_raw: true,
  conversion_note: null,
  source_kind: "document_upload",
  text: null,
  records: [],
  chunks: [],
  conversation: null,
});

function show(path: string) {
  const router = createMemoryRouter(
    [
      { path: "/knowledge", element: <KnowledgeManagementPage /> },
      {
        path: "/knowledge/:kb/sources/:kind/:sourceId",
        element: <KnowledgeSourcePage />,
      },
      {
        path: "/capture/:kb/:conversationId",
        element: <LegacyCaptureReviewRedirect />,
      },
    ],
    { initialEntries: [path] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("knowledge management", () => {
  it("extracts raw TXT directly with a per-source mode and reviews chunks in the shared workspace", async () => {
    let source = rawText();
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/extract")) {
        expect(JSON.parse(init?.body as string)).toEqual({ mode: "documents" });
        source = {
          ...source,
          stage: "extracted",
          mode: "documents",
          review: "pending",
          text: "Chưa xác nhận nguyên nhân.",
          processing: { status: "done" },
          counts: { draft: 1 },
          chunks: [
            {
              id: "evidence1#0",
              doc_id: "evidence1",
              title: "notes.txt",
              index: 0,
              text: "Chưa xác nhận nguyên nhân.",
              status: "draft",
              history: [],
            },
          ],
        };
        return Response.json({ status: "queued" });
      }
      if (url.endsWith("/review-chunk")) {
        expect(JSON.parse(init?.body as string)).toMatchObject({
          chunk_id: "evidence1#0",
          decision: "approve",
        });
        source = {
          ...source,
          review: "reviewed",
          counts: { approved: 1 },
          chunks: source.chunks.map((chunk) => ({
            ...chunk,
            status: "approved",
          })),
        };
        return Response.json(source);
      }
      if (url.endsWith("/sources/upload/upload1")) return Response.json(source);
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    show("/knowledge/general/sources/upload/upload1");
    await screen.findByTestId("extract-source");
    expect(screen.queryByTestId("convert-source")).not.toBeInTheDocument();
    expect(screen.getByText(/File TXT đã là văn bản/)).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("extraction-mode"), {
      target: { value: "documents" },
    });
    fireEvent.click(screen.getByTestId("extract-source"));
    await screen.findByTestId("document-chunk");
    expect(screen.getByText("1 mục chờ duyệt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Duyệt" }));
    await waitFor(() =>
      expect(screen.getByTestId("document-chunk")).toHaveAttribute(
        "data-status",
        "approved",
      ),
    );
    expect(
      screen.getByText("Đã duyệt xong", { exact: false }),
    ).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/convert"))).toBe(
      false,
    );
  });

  it("keeps OCR media raw and filters the shared source inventory", async () => {
    const media = {
      ...rawText(),
      id: "image1",
      name: "machine.png",
      content_type: "image/png",
      can_convert: false,
      can_extract_raw: false,
      conversion_note: "Chưa có OCR cho loại file này.",
    };
    const extracted = {
      ...rawText(),
      id: "voice1",
      kind: "conversation",
      name: "Ca đêm",
      stage: "extracted",
      review: "pending",
      counts: { draft: 2 },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/knowledge-bases"))
          return Response.json({
            knowledge_bases: [{ name: "general", description: "General" }],
          });
        if (url.includes("/sources?"))
          return Response.json({
            sources: [media, extracted],
            warnings: [],
            next_offset: null,
            qa_mode: "knowledge",
          });
        return Response.json(media);
      }),
    );
    show("/knowledge");
    await screen.findByText("machine.png");
    expect(screen.getByText("Ca đêm")).toBeInTheDocument();
    expect(screen.getByText(/Chưa có OCR/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Chờ duyệt" }));
    expect(screen.queryByText("machine.png")).not.toBeInTheDocument();
    expect(screen.getByText("Ca đêm")).toBeInTheDocument();
  });

  it("redirects old recording review links and reviews knowledge through the common API", async () => {
    const record = {
      id: "record1",
      subject: "Trục Z",
      claim: "Chưa xác nhận nguyên nhân.",
      type: "fact",
      status: "draft" as const,
      context: {},
      stated_by: null,
      created_at: "2026-10-23T00:00:00Z",
      superseded_by: null,
      verify: null,
      consolidation: null,
      history: [],
    };
    const source = {
      ...rawText(),
      id: "voice1",
      kind: "conversation" as const,
      stage: "extracted" as const,
      mode: "knowledge" as const,
      review: "pending" as const,
      processing: { status: "done" as const },
      text: record.claim,
      records: [record],
      counts: { draft: 1 },
      can_extract_raw: false,
    };
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/v1/knowledge-management/general/knowledge/record1/review")
        return Response.json({ ...record, status: "approved" });
      return Response.json(source);
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = show("/capture/general/voice1");
    await screen.findByTestId("approve-record");
    expect(router.state.location.pathname).toBe(
      "/knowledge/general/sources/conversation/voice1",
    );
    fireEvent.click(screen.getByTestId("approve-record"));
    await waitFor(() =>
      expect(screen.getByTestId("knowledge-record")).toHaveAttribute(
        "data-status",
        "approved",
      ),
    );
    expect(screen.getByText(/Đã duyệt xong/)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([url]) => url.includes("/v1/voice/") && url.endsWith("/review"),
      ),
    ).toBe(false);
  });
});
