import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConflictJobNote, ConflictReviewPanel } from "./conflict-review";
import type {
  ConflictPage,
  ConflictSide,
  KnowledgeConflict,
} from "@/services/knowledge-conflicts";
import type { SourceJob } from "@/services/knowledge-management";
import { sessionClient } from "@/services/auth-api";

const side = (id: string, claim: string): ConflictSide => ({
  id,
  subject: "FX5-PLC9001",
  claim,
  type: "rule",
  status: "draft",
  context: {
    machine_id: "FX5-PLC9001",
    configuration: "CONFLICT-DEMO",
    quotes: [{ turn_id: "T04", quote: claim }],
  },
  stated_by: "Chuyên gia",
  created_at: "2026-10-23T01:00:00Z",
  superseded_by: null,
  verify: null,
  consolidation: null,
  history: [],
  evidence_id: `source-${id}`,
  quote: claim,
});
const pair = (
  overrides: Partial<KnowledgeConflict> = {},
): KnowledgeConflict => ({
  id: "pair1",
  a_id: "record-a",
  b_id: "record-b",
  new_id: "record-b",
  status: "open",
  relation: "contradiction",
  aspect: "value",
  confidence: 0.95,
  reason: "Cùng phạm vi nhưng yêu cầu thời gian chờ khác nhau.",
  quotes: {},
  linked: true,
  confirmed_by_pair: true,
  review: [],
  created_at: "2026-10-23T01:00:00Z",
  updated_at: "2026-10-23T01:00:00Z",
  stale: false,
  a: side("record-a", "D100 bắt buộc bằng 1000 ms."),
  b: side("record-b", "D100 bắt buộc bằng 3000 ms."),
  ...overrides,
});
const page = (
  conflicts = [pair()],
  extra: Partial<ConflictPage> = {},
): ConflictPage => ({
  conflicts,
  counts: { open: 1, no_conflict: 2 },
  offset: 0,
  limit: 20,
  next_offset: null,
  enabled: true,
  ...extra,
});
function show(onChanged = vi.fn()) {
  return render(
    <MemoryRouter>
      <ConflictReviewPanel
        kb="industrial_qa"
        refreshKey="0"
        activeJobs={false}
        onChanged={onChanged}
      />
    </MemoryRouter>,
  );
}
const loaded = () =>
  screen.findByRole("button", { name: "Xác nhận mâu thuẫn" });
afterEach(() => {
  sessionClient.accessToken = null;
  vi.unstubAllGlobals();
});

describe("conflict review", () => {
  it("compares evidence and confirms without approving the draft records", async () => {
    sessionClient.accessToken = "conflict-review-test-token";
    const onChanged = vi.fn();
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe(
        "Bearer conflict-review-test-token",
      );
      if (url.endsWith("/review")) {
        expect(JSON.parse(init?.body as string)).toEqual({
          decision: "confirm",
          note: "Đã đối chiếu nguồn",
        });
        return Response.json(pair({ status: "confirmed" }));
      }
      return Response.json(page());
    });
    vi.stubGlobal("fetch", fetchMock);
    show(onChanged);
    await loaded();
    const a = screen.getByRole("region", { name: "Bản ghi A" });
    expect(within(a).getAllByText("D100 bắt buộc bằng 1000 ms.")).toHaveLength(
      3,
    );
    expect(
      within(a).getByRole("link", { name: "Mở nguồn bằng chứng" }),
    ).toHaveAttribute(
      "href",
      "/knowledge/industrial_qa/sources/conversation/source-record-a",
    );
    expect(within(a).getByText("CONFLICT-DEMO")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Đã đối chiếu nguồn" },
    });
    fireEvent.click(await loaded());
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(
      fetchMock.mock.calls.some(([url]) => url.endsWith("/pair1/review")),
    ).toBe(true);
    expect(screen.getAllByText("Chờ duyệt")).toHaveLength(2);
  });

  it("dismisses with a note and preserves the note after a rejected review", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        expect(JSON.parse(init.body as string)).toEqual({
          decision: "dismiss",
          note: "Cần kiểm tra",
        });
        return Response.json(
          { detail: "A newer judgement of this pair exists; reload the list" },
          { status: 409 },
        );
      }
      return Response.json(page());
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    await loaded();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Cần kiểm tra" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Bỏ qua" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Cặp này vừa được so sánh lại",
    );
    expect(screen.getByRole("textbox")).toHaveValue("Cần kiểm tra");
  });

  it("requires an explicit survivor before superseding and sends its ID", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        expect(JSON.parse(init.body as string)).toEqual({
          decision: "supersede",
          keep_id: "record-b",
          note: "",
        });
        return Response.json(pair({ status: "resolved" }));
      }
      return Response.json(page());
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    await loaded();
    fireEvent.click(
      screen.getByRole("button", { name: "Thay thế một bản ghi…" }),
    );
    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", {
      name: "Lưu quyết định thay thế",
    });
    expect(submit).toBeDisabled();
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("radio", { name: /Bản ghi B/ }));
    fireEvent.click(submit);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Thay thế bản ghi: đã lưu/)).toBeInTheDocument();
  });

  it("locks stale judgements and queues their evidence sources for comparison", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        expect(JSON.parse(init.body as string)).toEqual({
          record_ids: ["record-a", "record-b"],
        });
        return Response.json({ queued: [{}], busy: [], sources: 1, stale: 1 });
      }
      return Response.json(page([pair({ stale: true })]));
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    expect(await loaded()).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Kiểm tra lại cặp này" }),
    );
    await screen.findByText(/Đã xếp 1 nguồn để kiểm tra lại/);
    expect(
      fetchMock.mock.calls.some(([url]) => url.endsWith("/conflicts/recheck")),
    ).toBe(true);
  });

  it("filters and paginates, resetting the page when the filter changes", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const offset = new URL(url, "http://localhost").searchParams.get(
        "offset",
      );
      return Response.json(
        page([pair()], offset ? { offset: 20 } : { next_offset: 20 }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Trang sau" }));
    await waitFor(() =>
      expect(screen.getByText("Trang 2")).toBeInTheDocument(),
    );
    expect(
      fetchMock.mock.calls.some(([url]) => url.includes("offset=20")),
    ).toBe(true);
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "needs_review" },
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.at(-1)?.[0]).toMatch(/status=needs_review$/),
    );
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "all" },
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.at(-1)?.[0]).toMatch(/\/conflicts$/),
    );
  });

  it("handles disabled KBs, missing records, resolved pairs and API errors", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json(page([], { enabled: false })),
    );
    vi.stubGlobal("fetch", fetchMock);
    const view = show();
    await screen.findByText("Knowledge base này chưa bật phát hiện mâu thuẫn.");
    expect(
      screen.queryByRole("button", { name: "Kiểm tra lại bản ghi đã đổi" }),
    ).not.toBeInTheDocument();
    view.unmount();
    fetchMock.mockImplementation(async () =>
      Response.json(
        page([
          pair({ a: { id: "record-a", missing: true } }),
          pair({ id: "pair2", status: "resolved" }),
        ]),
      ),
    );
    const second = show();
    expect(await loaded()).toBeDisabled();
    expect(screen.getByText("Bản ghi không còn tồn tại.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Cặp này đã được xử lý bằng quyết định thay thế bản ghi.",
      ),
    ).toBeInTheDocument();
    second.unmount();
    fetchMock.mockImplementation(async () =>
      Response.json({ detail: "Unavailable" }, { status: 503 }),
    );
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503");
  });

  it("ignores a late response from the previous KB", async () => {
    let resolveOld!: (value: Response) => void;
    const old = new Promise<Response>((resolve) => {
      resolveOld = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.includes("industrial_qa")
          ? old
          : Response.json(page([], { enabled: false })),
      ),
    );
    const view = show();
    view.rerender(
      <MemoryRouter>
        <ConflictReviewPanel
          kb="general"
          refreshKey="0"
          activeJobs={false}
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    );
    await screen.findByText("Knowledge base này chưa bật phát hiện mâu thuẫn.");
    await act(async () => {
      resolveOld(Response.json(page()));
    });
    expect(
      screen.queryByRole("button", { name: "Xác nhận mâu thuẫn" }),
    ).not.toBeInTheDocument();
  });

  it("refreshes while jobs run and stops polling when they finish", async () => {
    const fetchMock = vi.fn(async () => Response.json(page()));
    vi.stubGlobal("fetch", fetchMock);
    const view = show();
    await loaded();
    vi.useFakeTimers();
    view.rerender(
      <MemoryRouter>
        <ConflictReviewPanel
          kb="industrial_qa"
          refreshKey="0"
          activeJobs
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    view.rerender(
      <MemoryRouter>
        <ConflictReviewPanel
          kb="industrial_qa"
          refreshKey="done"
          activeJobs={false}
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    const calls = fetchMock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(calls);
  });

  it("shows active or failed conflict jobs but hides completed summaries", () => {
    const job = {
      stage: "conflict",
      status: "failed",
      error: "Endpoint unavailable",
    } as SourceJob;
    const view = render(
      <ConflictJobNote processing={{ status: "done", conflict_job: job }} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Endpoint unavailable");
    view.rerender(
      <ConflictJobNote
        processing={{
          status: "done",
          conflict_job: { ...job, status: "running" },
        }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Đang kiểm tra mâu thuẫn",
    );
    view.rerender(
      <ConflictJobNote
        processing={{
          status: "done",
          conflicts: {
            status: "done",
            job_id: "j",
            finished_at: "",
            open: 2,
            pending: 1,
            truncated: true,
          },
        }}
      />,
    );
    expect(screen.queryByText(/cặp cần xem xét/)).not.toBeInTheDocument();
  });
});
