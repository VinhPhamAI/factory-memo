import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/hooks/auth";
import type { AuthClient } from "@/services/auth-api";
import type { Inquiry, InquirySummary } from "@/services/inquiries-api";
import { InquiriesPage } from "./inquiries";
import { InquiryPage } from "./inquiry";

const ME = "user-me";
const row = (over: Partial<InquirySummary>): InquirySummary => ({
  id: "i1",
  title: "Máy A lỗi",
  status: "draft",
  requested_by: ME,
  requester_name: "Tôi",
  assigned_to: null,
  assignee_name: null,
  created_at: "2026-11-05T08:00:00Z",
  updated_at: "2026-11-05T08:00:00Z",
  submitted_at: null,
  stage: "todo",
  ...over,
});
const detail = (over: Partial<Inquiry> = {}): Inquiry => ({
  ...row({}),
  conversation_id: "c1",
  context: {
    situation: "<b>Dừng máy</b>",
    observations: "",
    attempted_actions: "",
    proposed_fix: "",
    evidence: [{ kind: "citation", title: "Sổ tay", excerpt: "<i>x</i>" }],
  },
  files: [],
  events: [],
  ...over,
});

type Handler = (url: string, init?: RequestInit) => Response;
function mount(path: string, handler: Handler) {
  const authorizedFetch = vi.fn(async (url: string, init?: RequestInit) =>
    handler(url, init),
  );
  const client = {
    state: {
      status: "signed-in",
      user: { id: ME, username: "me", display_name: "Tôi", role: "employee" },
    },
    restore: async () => {},
    refresh: async () => false,
    subscribe: () => () => {},
    authorizedFetch,
  } as unknown as AuthClient;
  const router = createMemoryRouter(
    [
      { path: "/inquiries", element: <InquiriesPage /> },
      { path: "/inquiries/:id", element: <InquiryPage /> },
    ],
    { initialEntries: [path] },
  );
  render(
    <AuthProvider client={client}>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
  return authorizedFetch;
}

describe("inquiry inbox", () => {
  const rows = [
    row({ id: "d", title: "Nháp" }),
    row({
      id: "s",
      title: "Đã gửi 1",
      status: "open",
      assignee_name: "Chuyên gia",
    }),
  ];
  it("splits drafts and sent requests of the sent box", async () => {
    mount("/inquiries", () => Response.json({ inquiries: rows }));
    expect(await screen.findByText("Nháp")).toBeInTheDocument();
    expect(screen.queryByText("Đã gửi 1")).toBeNull();
    fireEvent.click(screen.getByTestId("tab-sent"));
    expect(await screen.findByText("Đã gửi 1")).toBeInTheDocument();
    expect(screen.queryByText("Nháp")).toBeNull();
  });
  it("loads the assigned box and shows empty state", async () => {
    const fetch = mount("/inquiries", (url) =>
      Response.json({ inquiries: url.includes("assigned") ? [] : rows }),
    );
    await screen.findByText("Nháp");
    fireEvent.click(screen.getByTestId("tab-assigned"));
    expect(await screen.findByTestId("inquiries-empty")).toHaveTextContent(
      "Chưa có yêu cầu nào được giao",
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("box=assigned"),
      expect.anything(),
    );
  });
});

describe("inquiry detail", () => {
  it("renders event content and evidence as text", async () => {
    mount("/inquiries/i1", () =>
      Response.json(
        detail({
          status: "needs_info",
          assigned_to: "exp",
          assignee_name: "E",
          events: [
            {
              id: "e1",
              kind: "question",
              content: "<script>x</script>",
              actor: { id: "exp", display_name: "E" },
              created_at: "2026-11-05T09:00:00Z",
            },
          ],
        }),
      ),
    );
    expect(await screen.findByText("<script>x</script>")).toBeInTheDocument();
    expect(screen.getByText("<b>Dừng máy</b>")).toBeInTheDocument();
    expect(screen.getByText("<i>x</i>")).toBeInTheDocument();
    // requester on needs_info may reply
    expect(screen.getByRole("button", { name: "Trả lời" })).toBeInTheDocument();
  });

  it("shows review actions to the assignee only", async () => {
    mount("/inquiries/i1", () =>
      Response.json(
        detail({ status: "open", requested_by: "other", assigned_to: ME }),
      ),
    );
    expect(
      await screen.findByRole("button", { name: "Chấp nhận" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Hỏi thêm" }),
    ).toBeInTheDocument();
  });

  it("hides review actions from the requester on open", async () => {
    mount("/inquiries/i1", () =>
      Response.json(detail({ status: "open", assigned_to: "exp" })),
    );
    await screen.findByTestId("inquiry-status");
    expect(screen.queryByTestId("review-actions")).toBeNull();
  });

  it("on 409 shows 'Trạng thái đã thay đổi' and reloads", async () => {
    let gets = 0;
    const fetch = mount("/inquiries/i1", (_url, init) => {
      if (init?.method === "POST")
        return Response.json(
          { detail: { code: "invalid_transition", message: "x" } },
          { status: 409 },
        );
      gets += 1;
      return Response.json(
        detail({ status: "open", requested_by: "other", assigned_to: ME }),
      );
    });
    fireEvent.click(await screen.findByRole("button", { name: "Chấp nhận" }));
    expect(
      await screen.findByText("Trạng thái đã thay đổi"),
    ).toBeInTheDocument();
    await waitFor(() => expect(gets).toBe(2));
    expect(fetch).toHaveBeenCalled();
  });

  it("draft: removing a file evidence makes one detach call", async () => {
    const calls: string[] = [];
    const draft = detail({
      context: {
        situation: "",
        observations: "",
        attempted_actions: "",
        proposed_fix: "",
        evidence: [{ kind: "file", title: "a.pdf", file_id: "f1" }],
      },
      files: [
        {
          file_id: "f1",
          filename: "a.pdf",
          content_type: "application/pdf",
          size_bytes: 1,
          event_id: null,
        },
      ],
    });
    mount("/inquiries/i1", (url, init) => {
      const method = init?.method ?? "GET";
      if (url.includes("/v1/experts")) return Response.json({ experts: [] });
      if (method === "GET") return Response.json(draft);
      calls.push(`${method} ${url}`);
      return new Response(null, { status: 204 });
    });
    const remove = await screen.findByRole("button", {
      name: "Xóa bằng chứng a.pdf",
    });
    // An unsaved edit must survive the detach and the refresh that follows.
    fireEvent.change(screen.getByLabelText("Tình huống"), {
      target: { value: "Chưa lưu" },
    });
    fireEvent.click(remove);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatch(/^DELETE .*files\/f1/);
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Xóa bằng chứng a.pdf" }),
      ).toBeNull(),
    );
    expect(screen.getByLabelText("Tình huống")).toHaveValue("Chưa lưu");
  });
});
