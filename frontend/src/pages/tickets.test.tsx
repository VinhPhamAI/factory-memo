import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/hooks/auth";
import type { AuthClient } from "@/services/auth-api";
import type { InquirySummary } from "@/services/inquiries-api";
import { TicketsPage } from "./tickets";

const ME = "user-me";
const row = (over: Partial<InquirySummary>): InquirySummary => ({
  id: "t1",
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

/** Mount the board; `rows` is what the sent box returns and changes after a demo proposal. */
function mount(rows: () => InquirySummary[]) {
  const authorizedFetch = vi.fn(async (url: string) =>
    Response.json(
      url.includes("demo-proposal")
        ? {}
        : { inquiries: url.includes("box=sent") ? rows() : [] },
    ),
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
    [{ path: "/tickets", element: <TicketsPage /> }],
    { initialEntries: ["/tickets"] },
  );
  render(
    <AuthProvider client={client}>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
  return authorizedFetch;
}

const transfer = () => ({
  setData: vi.fn(),
  effectAllowed: "",
  dropEffect: "",
});

describe("ticket board drag and drop", () => {
  it("analyzes a to-do ticket dropped on In progress", async () => {
    let analyzed = false;
    const fetch = mount(() => [
      row({ stage: analyzed ? "in_progress" : "todo" }),
    ]);
    fetch.mockImplementation(async (url: string) => {
      if (url.includes("demo-proposal")) {
        analyzed = true;
        return Response.json({});
      }
      return Response.json({
        inquiries: url.includes("box=sent")
          ? [row({ stage: analyzed ? "in_progress" : "todo" })]
          : [],
      });
    });

    const card = await screen.findByTestId("ticket-card");
    expect(card).toHaveAttribute("draggable", "true");
    const target = screen.getByTestId("ticket-column-in_progress");
    expect(target).not.toHaveAttribute("data-drop");

    fireEvent.dragStart(card, { dataTransfer: transfer() });
    expect(target).toHaveAttribute("data-drop", "ready");
    fireEvent.dragOver(target, { dataTransfer: transfer() });
    expect(target).toHaveAttribute("data-drop", "over");
    fireEvent.drop(target, { dataTransfer: transfer() });

    // The card moves at once and stays dimmed while the proposal is created.
    const dropped = within(target).getByTestId("ticket-card");
    expect(dropped).toHaveAttribute("aria-busy", "true");
    expect(dropped).not.toHaveAttribute("draggable", "true");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("demo-proposal"),
      expect.objectContaining({ method: "POST" }),
    );

    await waitFor(
      () =>
        expect(
          within(screen.getByTestId("ticket-column-in_progress")).getByTestId(
            "ticket-card",
          ),
        ).not.toHaveAttribute("aria-busy"),
      { timeout: 4000 },
    );
    expect(screen.queryByText("Đang phân tích…")).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId("ticket-column-in_progress")).getByTestId(
        "ticket-card",
      ),
    ).toBeInTheDocument();
  });

  it("does not offer a drop for other people's tickets or later stages", async () => {
    mount(() => [
      row({ id: "a", requested_by: "other", requester_name: "Khác" }),
      row({ id: "b", stage: "in_review", status: "open" }),
    ]);
    const cards = await screen.findAllByTestId("ticket-card");
    for (const card of cards) {
      expect(card).not.toHaveAttribute("draggable", "true");
      fireEvent.dragStart(card, { dataTransfer: transfer() });
    }
    expect(screen.getByTestId("ticket-column-in_progress")).not.toHaveAttribute(
      "data-drop",
    );
  });
});
