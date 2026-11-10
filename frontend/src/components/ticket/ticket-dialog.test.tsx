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

describe("TicketDialog", () => {
  it("links to the conversation the ticket came from", async () => {
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
        Response.json(url.includes("/v1/experts") ? { experts: [] } : ticket),
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
    const link = await screen.findByTestId("ticket-conversation-link");
    expect(link).toHaveAttribute("href", "/ask/c-123");
    expect(link).toHaveTextContent("Cuộc hội thoại gốc");
  });
});
