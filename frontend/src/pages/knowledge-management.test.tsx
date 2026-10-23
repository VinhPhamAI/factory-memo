import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  KnowledgeManagementPage,
  KnowledgeSourcePage,
  LegacyCaptureReviewRedirect,
} from "./knowledge-management";
import type {
  ManagedSourceDetail,
  SourceJob,
} from "@/services/knowledge-management";

const job = (overrides: Partial<SourceJob> = {}): SourceJob => ({
  id: "job1",
  stage: "convert",
  status: "queued",
  mode: "documents",
  next_stages: ["extract"],
  attempts: 0,
  max_attempts: 3,
  error: null,
  created_at: "2026-10-23T00:00:00Z",
  started_at: null,
  finished_at: null,
  retry_at: null,
  ...overrides,
});

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
  it("queues Extract on raw TXT (convert, then extract) with a per-source mode and reviews chunks", async () => {
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
        return Response.json({
          status: "queued",
          mode: "documents",
          job: job(),
        });
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
    expect(screen.getByTestId("convert-source")).toBeInTheDocument();
    expect(screen.getByTestId("extract-source")).toHaveTextContent(
      "Chuyển đổi & trích xuất",
    );
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

  it("queues Convert and Extract for the checked sources from the bulk bar", async () => {
    const raw = rawText();
    const converted = {
      ...rawText(),
      id: "voice2",
      kind: "conversation" as const,
      name: "Ca sáng",
      stage: "converted" as const,
      can_convert: false,
      can_extract_raw: false,
    };
    const media = {
      ...rawText(),
      id: "image1",
      name: "machine.png",
      can_convert: false,
      can_extract_raw: false,
      conversion_note: "Chưa có OCR cho loại file này.",
    };
    const running = {
      ...rawText(),
      id: "upload3",
      name: "running.txt",
      processing: {
        status: "running" as const,
        job: job({ status: "running" }),
      },
    };
    const requests: { url: string; body?: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST")
          requests.push({
            url,
            body: init.body ? JSON.parse(init.body as string) : undefined,
          });
        if (url.includes("/knowledge-bases"))
          return Response.json({
            knowledge_bases: [{ name: "general", description: "General" }],
          });
        if (url.includes("/sources?"))
          return Response.json({
            sources: [raw, converted, media, running],
            warnings: [],
            next_offset: null,
            qa_mode: "documents",
          });
        if (url.endsWith("/upload/upload1/extract"))
          return Response.json(
            {
              status: "queued",
              mode: "knowledge",
              job: job({ mode: "knowledge" }),
            },
            { status: 202 },
          );
        if (url.endsWith("/conversation/voice2/extract"))
          return Response.json({ detail: "busy" }, { status: 409 });
        if (url.endsWith("/upload/upload1/convert"))
          return Response.json(
            { job: job({ next_stages: [] }) },
            { status: 202 },
          );
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    show("/knowledge");
    await screen.findByText("notes.txt");
    // No per-row buttons; only sources with a queueable step can be checked.
    expect(screen.queryByTestId("row-convert")).toBeNull();
    expect(screen.queryByTestId("row-extract")).toBeNull();
    const [rawBox, convertedBox, mediaBox, runningBox] =
      screen.getAllByTestId("select-source");
    expect(mediaBox).toBeDisabled();
    expect(runningBox).toBeDisabled();
    expect(screen.getByTestId("bulk-convert")).toBeDisabled();
    expect(screen.getByTestId("bulk-extract")).toBeDisabled();

    fireEvent.click(screen.getByTestId("select-all-sources"));
    expect(rawBox).toBeChecked();
    expect(convertedBox).toBeChecked();
    expect(screen.getByTestId("bulk-convert")).toHaveTextContent("Convert (1)");
    expect(screen.getByTestId("bulk-extract")).toHaveTextContent("Extract (2)");
    // The mode defaults to the KB's QA retrieval mode.
    expect(screen.getByTestId("bulk-extraction-mode")).toHaveValue("documents");
    fireEvent.change(screen.getByTestId("bulk-extraction-mode"), {
      target: { value: "knowledge" },
    });
    fireEvent.click(screen.getByTestId("bulk-extract"));

    const result = await screen.findByTestId("bulk-result");
    expect(result).toHaveTextContent("Đã xếp 1 job trích xuất");
    expect(result).toHaveTextContent("Ca sáng: Nguồn đang được xử lý");
    expect(requests).toEqual([
      {
        url: "/v1/knowledge-management/general/sources/upload/upload1/extract",
        body: { mode: "knowledge" },
      },
      {
        url: "/v1/knowledge-management/general/sources/conversation/voice2/extract",
        body: { mode: "knowledge" },
      },
    ]);
    // The queued raw TXT converts first; the failed source stays checked.
    expect(screen.getAllByTestId("source-progress")[0]).toHaveTextContent(
      "Đang chờ chuyển đổi → sau đó trích xuất",
    );
    expect(rawBox).toBeDisabled();
    expect(rawBox).not.toBeChecked();
    expect(convertedBox).toBeChecked();
    expect(screen.getByTestId("bulk-convert")).toBeDisabled();
    expect(screen.getByTestId("bulk-extract")).toHaveTextContent("Extract (1)");
  });

  it("queues Convert only for the checked sources that have a converter", async () => {
    const raw = rawText();
    const other = { ...rawText(), id: "upload2", name: "log.csv" };
    const converted = {
      ...rawText(),
      id: "voice2",
      kind: "conversation" as const,
      name: "Ca sáng",
      stage: "converted" as const,
      can_convert: false,
    };
    const posted: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST") posted.push(url);
        if (url.includes("/knowledge-bases"))
          return Response.json({
            knowledge_bases: [{ name: "general", description: "General" }],
          });
        if (url.includes("/sources?"))
          return Response.json({
            sources: [raw, other, converted],
            warnings: [],
            next_offset: null,
            qa_mode: "knowledge",
          });
        if (url.endsWith("/convert"))
          return Response.json(
            { job: job({ next_stages: [] }) },
            { status: 202 },
          );
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    show("/knowledge");
    await screen.findByText("log.csv");
    const boxes = screen.getAllByTestId("select-source");
    fireEvent.click(boxes[1]);
    fireEvent.click(boxes[2]);
    expect(screen.getByTestId("select-all-sources")).not.toBeChecked();
    expect(screen.getByTestId("bulk-convert")).toHaveTextContent("Convert (1)");
    fireEvent.click(screen.getByTestId("bulk-convert"));
    expect(await screen.findByTestId("bulk-result")).toHaveTextContent(
      "Đã xếp 1 job chuyển đổi vào hàng đợi; bỏ qua 1 mục không hỗ trợ bước này.",
    );
    expect(posted).toEqual([
      "/v1/knowledge-management/general/sources/upload/upload2/convert",
    ]);
    expect(screen.getByTestId("source-progress")).toHaveTextContent(
      "Đang chờ chuyển đổi",
    );
    expect(boxes[0]).not.toBeChecked();
    expect(boxes[2]).toBeChecked();
  });

  it("shows a running stage, a scheduled retry and the final failure of a job", async () => {
    let source: ManagedSourceDetail = {
      ...rawText(),
      stage: "converted",
      text: "Ghi chú ca đêm.",
      processing: {
        status: "queued",
        job: job({
          stage: "extract",
          attempts: 1,
          next_stages: [],
          error: "Dịch vụ LLM không phản hồi",
          retry_at: "2026-10-23T00:01:00Z",
        }),
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(source)),
    );
    show("/knowledge/general/sources/upload/upload1");
    expect(await screen.findByTestId("source-progress")).toHaveTextContent(
      "Đang chờ trích xuất",
    );
    expect(screen.getByText(/Tự thử lại lúc/)).toHaveTextContent(
      "Lần thử 1/3 lỗi: Dịch vụ LLM không phản hồi",
    );
    expect(screen.getByTestId("extract-source")).toBeDisabled();
    source = {
      ...source,
      processing: {
        status: "failed",
        error: "Dịch vụ LLM không phản hồi",
        job: job({ stage: "extract", status: "failed", attempts: 3 }),
      },
    };
    fireEvent.click(screen.getByRole("button", { name: "Làm mới" }));
    expect(
      await screen.findByText(/Trích xuất lỗi \(lần thử 3\/3\)/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("extract-source")).not.toBeDisabled();
  });
});
