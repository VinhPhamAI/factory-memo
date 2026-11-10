import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/hooks/auth";
import type { AuthClient } from "@/services/auth-api";
import { TicketDialog } from "./ticket-dialog";

const ticket = {
  id: "t1",
  title: "Máy A lỗi",
  status: "draft",
  requested_by: "user-me",
  requester_name: "Tôi",
  assigned_to: null,
  assignee_name: null,
  created_at: "2026-11-05T08:00:00Z",
  updated_at: "2026-11-05T08:00:00Z",
  submitted_at: null,
  stage: "todo",
  conversation_id: "c-123",
  context: {
    situation: "Máy dừng",
    observations: "",
    attempted_actions: "",
    proposed_fix: "",
    evidence: [],
  },
  files: [],
  events: [],
};

function renderDialog(data: typeof ticket) {
  const client = {
    state: {
      status: "signed-in",
      user: {
        id: "user-me",
        username: "me",
        display_name: "Tôi",
        role: "employee",
      },
    },
    restore: async () => {},
    refresh: async () => false,
    subscribe: () => () => {},
    authorizedFetch: vi.fn(async (url: string) =>
      Response.json(url.includes("/v1/experts") ? { experts: [] } : data),
    ),
  } as unknown as AuthClient;
  render(
    <AuthProvider client={client}>
      <MemoryRouter>
        <TicketDialog
          ticketId="t1"
          userId="user-me"
          onClose={() => {}}
          onChanged={() => {}}
          notify={() => {}}
        />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("TicketDialog", () => {
  it("links to the conversation the ticket came from", async () => {
    renderDialog(ticket);
    const link = await screen.findByTestId("ticket-conversation-link");
    expect(link).toHaveAttribute("href", "/ask/c-123");
    expect(link).toHaveTextContent("Cuộc hội thoại gốc");
  });

  it("renders the AI proposal as markdown", async () => {
    renderDialog({
      ...ticket,
      context: {
        ...ticket.context,
        proposed_fix:
          "[Đề xuất AI – demo]\nCác bước:\n1. Ngắt nguồn\n2. Đo **120 Ω**\n\nCăn cứ:\n- Sổ tay trang 37",
      },
    });
    const proposal = await screen.findByTestId("ticket-proposal");
    expect(proposal.querySelectorAll("ol > li")).toHaveLength(2);
    expect(proposal.querySelectorAll("ul > li")).toHaveLength(1);
    expect(proposal.querySelector("strong")).toHaveTextContent("120 Ω");
  });
});
