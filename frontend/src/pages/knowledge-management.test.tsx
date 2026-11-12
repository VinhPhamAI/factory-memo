import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { KnowledgeNavigation } from "@/components/knowledge-navigation";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  KnowledgeManagementPage,
  KnowledgeSourcePage,
  LegacyCaptureReviewRedirect,
} from "./knowledge-management";
import type {
  DuplicateOf,
  ManagedSourceDetail,
  OverlapMatch,
  SourceJob,
} from "@/services/knowledge-management";

const { useAuth } = vi.hoisted(() => ({
  useAuth: vi.fn((): { status: "signed-out" } | {
    status: "signed-in";
    user: { id: string; role: string };
  } => ({ status: "signed-out" })),
}));
vi.mock("@/hooks/auth", () => ({ useAuth }));

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

const earlier = (overrides: Partial<DuplicateOf> = {}): DuplicateOf => ({
  run_id: "run1",
  evidence_id: "evidence0",
  status: "done",
  mode: "knowledge",
  name: "ca-dem.txt",
  kind: "upload",
  source_id: "upload0",
  same_logic: true,
  extracted_at: "2026-10-22T00:00:00Z",
  ...overrides,
});

const duplicateResponse = (duplicate: DuplicateOf) =>
  Response.json(
    {
      detail: {
        code: "duplicate_content",
        message: `Nội dung này đã được trích xuất ở «${duplicate.name}».`,
        duplicate_of: duplicate,
      },
    },
    { status: 409 },
  );

const match = (overrides: Partial<OverlapMatch> = {}): OverlapMatch => ({
  run_id: "run1",
  evidence_id: "evidence0",
  status: "done",
  mode: "knowledge",
  name: "ca-dem.txt",
  kind: "upload",
  source_id: "upload0",
  extracted_at: "2026-10-22T00:00:00Z",
  level: "near_duplicate",
  jaccard: 0.94,
  containment: 0.97,
  coverage: 0.96,
  ...overrides,
});

const nearDuplicateResponse = (matches: OverlapMatch[]) =>
  Response.json(
    {
      detail: {
        code: "near_duplicate",
        message: `Nội dung gần như trùng với «${matches[0].name}».`,
        matches,
      },
    },
    { status: 409 },
  );

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

function kbDetail(name = "general") {
  return Response.json({
    name, title: name, description: "", template: "general",
    created_by: "expert", created_at: "2026-10-23T00:00:00Z", updated_at: "2026-10-23T00:00:00Z",
    source_counts: {}, chunk_counts: {},
  });
}

function show(path: string, state?: unknown) {
  const router = createMemoryRouter(
    [
      {
        element: <><KnowledgeNavigation /><Outlet /></>,
        children: [{ path: "/knowledge", element: <KnowledgeManagementPage /> }],
      },
      {
        path: "/knowledge/:kb/sources/:kind/:sourceId",
        element: <KnowledgeSourcePage />,
      },
      {
        path: "/capture/:kb/:conversationId",
        element: <LegacyCaptureReviewRedirect />,
      },
    ],
    {
      initialEntries: [
        {
          pathname: path.split("?")[0],
          search: path.includes("?") ? `?${path.split("?")[1]}` : "",
          state,
        },
      ],
    },
  );
  render(<RouterProvider router={router} />);
  return router;
}

afterEach(() => {
  useAuth.mockReturnValue({ status: "signed-out" });
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("knowledge management", () => {
  it("renames a knowledge base, requires its name to delete, and switches the selector", async () => {
    useAuth.mockReturnValue({ status: "signed-in", user: { id: "1", role: "admin" } });
    let title = "General";
    let deleted = false;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/knowledge-bases")) return Response.json({
        knowledge_bases: [
          ...(deleted ? [] : [{ name: "general", title, description: "" }]),
          { name: "backup", title: "Backup", description: "" },
        ],
      });
      if (url.includes("/sources?")) return Response.json({
        sources: [], warnings: [], next_offset: null, qa_mode: "knowledge",
      });
      const name = url.endsWith("/backup") ? "backup" : "general";
      if (init?.method === "PATCH") {
        const fields = JSON.parse(init.body as string);
        expect(fields).toEqual({ title: "Line A", description: "" });
        title = fields.title;
      }
      if (init?.method === "DELETE") {
        expect(name).toBe("general");
        deleted = true;
        return new Response(null, { status: 204 });
      }
      return Response.json({
        name, title: name === "general" ? title : "Backup", template: "general",
        description: "", created_by: "expert", created_at: "2026-10-23T00:00:00Z",
        updated_at: "2026-10-23T00:00:00Z",
        source_counts: { received: 2 }, chunk_counts: { knowledge: { draft: 1 } },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    show("/knowledge");

    await screen.findByRole("option", { name: "General" });
    expect(screen.queryByRole("button", { name: "Sửa thông tin" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Xóa kho" })).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("link", { name: "Knowledge base" }));
    expect(await screen.findByText("Đã nhận 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sửa thông tin" }));
    fireEvent.change(screen.getByLabelText("Tiêu đề"), { target: { value: "Line A" } });
    fireEvent.click(screen.getByRole("button", { name: "Lưu thay đổi" }));
    await screen.findByRole("option", { name: "Line A" });
    fireEvent.click(screen.getByRole("button", { name: "Xóa kho" }));
    const confirm = screen.getByRole("button", { name: "Xác nhận xóa" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Mã xác nhận"), { target: { value: "general" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.getByTestId("management-kb")).toHaveValue("backup"));
    await user.click(screen.getByRole("link", { name: "Nguồn dữ liệu" }));
    expect(screen.queryByRole("button", { name: "Sửa thông tin" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Xóa kho" })).toBeNull();
  });

  it("creates a knowledge base from its own tab and selects the result", async () => {
    useAuth.mockReturnValue({ status: "signed-in", user: { id: "1", role: "expert" } });
    const created = { name: "may_ep", title: "Máy ép", description: "" };
    let hasCreated = false;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/v1/knowledge-bases") && init?.method === "POST") {
        expect(JSON.parse(init.body as string)).toEqual(created);
        hasCreated = true;
        return Response.json(created, { status: 201 });
      }
      if (url.endsWith("/knowledge-agent/knowledge-bases")) {
        return Response.json({ knowledge_bases: hasCreated ? [created] : [] });
      }
      if (url.endsWith("/knowledge-bases/may_ep")) return kbDetail(created.name);
      if (url.includes("/sources?")) return Response.json({
        sources: [], warnings: [], next_offset: null, qa_mode: "knowledge",
      });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    show("/knowledge?tab=knowledge-bases");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^Tạo knowledge base$/ }));
    await user.type(screen.getByTestId("kb-title"), "Máy ép");
    expect(screen.getByTestId("kb-name")).toHaveValue("may_ep");
    await user.click(screen.getByTestId("kb-create"));
    await screen.findByRole("option", { name: "Máy ép" });
    expect(screen.getByTestId("management-kb")).toHaveValue("may_ep");
    expect(screen.queryByRole("button", { name: "Xóa kho" })).toBeNull();
  });

  it("lets the submitter remove a queued source and disables removal for running jobs", async () => {
    useAuth.mockReturnValue({ status: "signed-in", user: { id: "owner", role: "employee" } });
    const source = { ...rawText(), submitted_by: "owner" };
    const running = {
      ...source, id: "running", name: "Running", processing: { job: job({ status: "running" }) },
    };
    let removed = false;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/knowledge-bases")) return Response.json({
        knowledge_bases: [{ name: "general", title: "General", description: "" }],
      });
      if (url.endsWith("/knowledge-bases/general")) return Response.json({
        name: "general", title: "General", description: "", template: "general",
        created_by: "expert", created_at: "2026-10-23T00:00:00Z", updated_at: "2026-10-23T00:00:00Z",
        source_counts: {}, chunk_counts: {},
      });
      if (init?.method === "DELETE") {
        expect(url).toContain("/sources/upload/upload1");
        removed = true;
        return new Response(null, { status: 204 });
      }
      return Response.json({
        sources: removed ? [running] : [source, running], warnings: [], next_offset: null,
        qa_mode: "knowledge",
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    show("/knowledge");
    const [allowed, disabled] = await screen.findAllByRole("button", { name: "Gỡ nguồn" });
    expect(disabled).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Sửa thông tin" })).not.toBeInTheDocument();
    fireEvent.click(allowed);
    fireEvent.click(screen.getByRole("button", { name: "Xác nhận gỡ" }));
    await waitFor(() => expect(screen.queryByText("notes.txt")).not.toBeInTheDocument());
  });

  it("opens the existing source and reports a single duplicate upload", async () => {
    const source = rawText();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/knowledge-bases")) return Response.json({
        knowledge_bases: [{ name: "general", title: "General", description: "" }],
      });
      if (url.endsWith("/uploads")) return Response.json({ ...source, duplicate: true });
      if (url.endsWith("/sources/upload/upload1")) return Response.json(source);
      if (url.includes("/sources?")) return Response.json({
        sources: [source], warnings: [], next_offset: null, qa_mode: "knowledge",
      });
      return Response.json({
        name: "general", title: "General", description: "", template: "general",
        created_by: "expert", created_at: "2026-10-23T00:00:00Z", updated_at: "2026-10-23T00:00:00Z",
        source_counts: {}, chunk_counts: {},
      });
    }));
    const router = show("/knowledge");
    await screen.findByRole("option", { name: "General" });
    fireEvent.click(screen.getByText("Thêm tài liệu"));
    fireEvent.change(screen.getByTestId("document-files"), {
      target: { files: [new File(["same bytes"], "renamed.txt")] },
    });
    fireEvent.click(screen.getByTestId("document-upload"));
    expect(await screen.findByText("File đã có trong KB")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/knowledge/general/sources/upload/upload1");
  });

  it("returns to the page the source was opened from, and to the library otherwise", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(rawText())));
    const path = "/knowledge/general/sources/upload/upload1";
    show(path, { from: "/admin/knowledge-bases?kb=general" });
    const back = await screen.findByRole("link", { name: /Kho kiến thức/ });
    expect(back).toHaveAttribute("href", "/admin/knowledge-bases?kb=general");
    cleanup();
    show(path, { from: "https://evil.example/" });
    expect(await screen.findByRole("link", { name: /Thư viện tài liệu/ })).toHaveAttribute("href", "/files");
  });

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
    expect(
      screen.getByText(/chuyển tài liệu thành văn bản và tìm kiến thức/),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("extraction-mode"), {
      target: { value: "documents" },
    });
    fireEvent.click(screen.getByTestId("extract-source"));
    await screen.findByTestId("document-chunk");
    expect(screen.queryByText("1 mục chờ duyệt")).not.toBeInTheDocument();
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
        if (url.endsWith("/v1/knowledge-bases/general")) return kbDetail();
        if (url.endsWith("/knowledge-bases"))
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
        if (url.endsWith("/v1/knowledge-bases/general")) return kbDetail();
        if (url.endsWith("/knowledge-bases"))
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
        if (url.endsWith("/v1/knowledge-bases/general")) return kbDetail();
        if (url.endsWith("/knowledge-bases"))
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
    // The step the job is producing is marked; finished steps are not.
    const steps = within(
      screen.getByRole("list", { name: "Tiến trình nguồn dữ liệu" }),
    );
    expect(steps.getByText("Đang chờ trích xuất").closest("li")).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(steps.getByText("Đã có văn bản").closest("li")).not.toHaveAttribute(
      "aria-current",
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
    expect(
      within(screen.getByRole("list", { name: "Tiến trình nguồn dữ liệu" }))
        .getByText("Đã trích xuất")
        .closest("li"),
    ).not.toHaveAttribute("aria-current");
  });
  it("reports identical content already extracted elsewhere instead of queueing it", async () => {
    const raw = rawText();
    const copy = {
      ...rawText(),
      id: "upload2",
      name: "ca-dem (2).txt",
      stage: "converted" as const,
      processing: {
        status: "duplicate" as const,
        duplicate_of: earlier(),
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/knowledge-bases/general")) return kbDetail();
        if (url.endsWith("/knowledge-bases"))
          return Response.json({
            knowledge_bases: [{ name: "general", description: "General" }],
          });
        if (url.includes("/sources?"))
          return Response.json({
            sources: [raw, copy],
            warnings: [],
            next_offset: null,
            qa_mode: "knowledge",
          });
        if (url.endsWith("/upload/upload1/extract"))
          return duplicateResponse(earlier({ status: "running" }));
        if (url.endsWith("/upload/upload2/extract"))
          return duplicateResponse(earlier({ same_logic: false }));
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    show("/knowledge");
    await screen.findByText("notes.txt");
    // The worker already skipped this copy; the chip names the earlier source.
    expect(screen.getByTestId("duplicate-chip")).toHaveAttribute(
      "title",
      "Trùng với «ca-dem.txt»",
    );
    fireEvent.click(screen.getByTestId("select-all-sources"));
    fireEvent.click(screen.getByTestId("bulk-extract"));

    const result = await screen.findByTestId("bulk-result");
    expect(result).toHaveTextContent(
      "Đã xếp 0 job trích xuất vào hàng đợi; 2 mục trùng nội dung đã trích xuất (không xếp hàng).",
    );
    const items = screen.getByTestId("bulk-duplicates").querySelectorAll("li");
    expect(items[0]).toHaveTextContent(
      "notes.txt: trùng với «ca-dem.txt» (đang trích xuất)",
    );
    expect(items[1]).toHaveTextContent(
      "ca-dem (2).txt: trùng với «ca-dem.txt» (logic trích xuất cũ)",
    );
    expect(items[0].querySelector("a")).toHaveAttribute(
      "href",
      "/knowledge/general/sources/upload/upload0",
    );
    // Duplicates would be refused again, so they are no longer checked.
    const boxes = screen.getAllByTestId("select-source");
    expect(boxes[0]).not.toBeChecked();
    expect(boxes[1]).not.toBeChecked();
  });

  it("links the earlier source when Extract is refused as a duplicate", async () => {
    const source = {
      ...rawText(),
      stage: "converted" as const,
      text: "Ghi chú.",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/extract")) return duplicateResponse(earlier());
        return Response.json(source);
      }),
    );
    show("/knowledge/general/sources/upload/upload1");
    fireEvent.click(await screen.findByTestId("extract-source"));
    const note = await screen.findByTestId("duplicate-note");
    expect(note).toHaveTextContent(
      "Nội dung trùng với «ca-dem.txt» đã trích xuất lúc",
    );
    expect(note).toHaveTextContent(
      "Không trích xuất lặp; xem và duyệt kết quả ở nguồn đó.",
    );
    expect(note.querySelector("a")).toHaveAttribute(
      "href",
      "/knowledge/general/sources/upload/upload0",
    );
    expect(screen.queryByText(/Nguồn đang được xử lý/)).toBeNull();
  });

  it("asks for confirmation before extracting content that nearly matches an extracted source", async () => {
    const source = {
      ...rawText(),
      stage: "converted" as const,
      text: "Ghi chú.",
    };
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/extract")) {
          const body = JSON.parse(String(init?.body));
          bodies.push(body);
          if (!body.confirm_overlap)
            return nearDuplicateResponse([
              match(),
              match({
                run_id: "run2",
                name: "so-tay.txt",
                source_id: "upload9",
                level: "similar",
                jaccard: 0.3,
                containment: 0.65,
                coverage: 0.31,
              }),
            ]);
          return Response.json({
            status: "queued",
            mode: "knowledge",
            job: job({ stage: "extract", mode: "knowledge", next_stages: [] }),
          });
        }
        return Response.json(source);
      }),
    );
    show("/knowledge/general/sources/upload/upload1");
    fireEvent.click(await screen.findByTestId("extract-source"));
    const note = await screen.findByTestId("overlap-note");
    expect(note).toHaveTextContent(
      "Chưa trích xuất: nội dung gần giống nguồn đã trích xuất (Mục tri thức).",
    );
    const items = note.querySelectorAll("li");
    expect(items[0]).toHaveTextContent(
      "«ca-dem.txt» Gần như trùng: Jaccard 94% · 97% nội dung này nằm trong nguồn đó",
    );
    expect(items[1]).toHaveTextContent("«so-tay.txt» Trùng một phần");
    expect(items[0].querySelector("a")).toHaveAttribute(
      "href",
      "/knowledge/general/sources/upload/upload0",
    );
    expect(screen.queryByText(/Nguồn đang được xử lý/)).toBeNull();

    fireEvent.click(screen.getByTestId("confirm-overlap"));
    expect(
      await screen.findByText(/không bị gộp hay ghi đè/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-overlap-submit"));
    await waitFor(() =>
      expect(bodies).toEqual([
        { mode: "knowledge" },
        { mode: "knowledge", confirm_overlap: true },
      ]),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("overlap-note")).toBeNull(),
    );
  });

  it("lists near-duplicates of a bulk Extract for confirmation per source", async () => {
    const raw = rawText();
    const waiting = {
      ...rawText(),
      id: "upload2",
      name: "ca-dem (ghi lại).txt",
      stage: "converted" as const,
      processing: {
        status: "near_duplicate" as const,
        mode: "knowledge" as const,
        overlaps: [match()],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/knowledge-bases/general")) return kbDetail();
        if (url.endsWith("/knowledge-bases"))
          return Response.json({
            knowledge_bases: [{ name: "general", description: "General" }],
          });
        if (url.includes("/sources?"))
          return Response.json({
            sources: [raw, waiting],
            warnings: [],
            next_offset: null,
            qa_mode: "knowledge",
          });
        if (url.endsWith("/upload/upload1/extract"))
          return Response.json({
            status: "queued",
            mode: "knowledge",
            job: job({ mode: "knowledge" }),
          });
        if (url.endsWith("/upload/upload2/extract"))
          return nearDuplicateResponse([
            match({ level: "contained", jaccard: 0.4 }),
            match({ run_id: "run2", name: "so-tay.txt" }),
          ]);
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    show("/knowledge");
    await screen.findByText("notes.txt");
    // The worker left this source waiting for confirmation.
    expect(screen.getByTestId("near-duplicate-chip")).toHaveTextContent(
      "Gần giống · chờ xác nhận",
    );
    fireEvent.click(screen.getByTestId("select-all-sources"));
    fireEvent.click(screen.getByTestId("bulk-extract"));

    const result = await screen.findByTestId("bulk-result");
    expect(result).toHaveTextContent(
      "Đã xếp 1 job trích xuất vào hàng đợi; 1 mục gần giống nguồn đã trích xuất, chờ xác nhận (mở từng nguồn để xác nhận).",
    );
    const item = screen.getByTestId("bulk-near-duplicates").querySelector("li");
    expect(item).toHaveTextContent(
      "ca-dem (ghi lại).txt: nằm trong «ca-dem.txt» và 1 nguồn khác",
    );
    expect(item?.querySelector("a")).toHaveAttribute(
      "href",
      "/knowledge/general/sources/upload/upload2",
    );
    const boxes = screen.getAllByTestId("select-source");
    expect(boxes[1]).not.toBeChecked();
  });

  it("shows overlaps of a finished extraction as information only", async () => {
    const source = {
      ...rawText(),
      stage: "extracted" as const,
      text: "Ghi chú.",
      processing: {
        status: "done" as const,
        mode: "knowledge" as const,
        overlaps: [match()],
        overlap_confirmed: true,
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(source)),
    );
    show("/knowledge/general/sources/upload/upload1");
    const info = await screen.findByTestId("overlap-info");
    expect(info).toHaveTextContent(
      "Đã trích xuất theo xác nhận dù nội dung gần giống:",
    );
    expect(screen.queryByTestId("confirm-overlap")).toBeNull();
  });
});

it.each(["search", "conflicts"])("shows the source inventory for the removed %s view", async (tab) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/knowledge-bases")) return Response.json({
      knowledge_bases: [{ name: "general", title: "General", description: "" }],
    });
    return Response.json({ sources: [], warnings: [], next_offset: null, qa_mode: "knowledge" });
  }));
  show(`/knowledge?tab=${tab}`);
  await screen.findByText("Chưa có nguồn dữ liệu");
  expect(screen.queryByRole("link", { name: "Tìm kiếm" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Mâu thuẫn" })).toBeNull();
  expect(screen.getByRole("link", { name: "Nguồn dữ liệu" })).toHaveAttribute("aria-current", "page");
});
