import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Search } from "lucide-react";
import { useAuth } from "@/hooks/auth";
import { useInquiriesApi } from "@/hooks/inquiries";
import { Input } from "@/components/ui/input";
import { ToastStack, useToasts } from "@/components/ui/toast";
import { TicketCard } from "@/components/ticket/ticket-card";
import { TicketDialog } from "@/components/ticket/ticket-dialog";
import {
  InquiryError,
  type InquiriesApi,
  type InquirySummary,
} from "@/services/inquiries-api";
import { STAGES } from "@/services/tickets";

type Filter = "all" | "mine" | "assigned";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "Tất cả" },
  { id: "mine", label: "Của tôi" },
  { id: "assigned", label: "Được giao" },
];

/** Load the user's sent and assigned tickets once each, merged by ID. */
async function loadBoard(api: InquiriesApi): Promise<InquirySummary[]> {
  const [sent, assigned] = await Promise.all([
    api.list("sent"),
    api.list("assigned"),
  ]);
  const unique = new Map<string, InquirySummary>();
  for (const ticket of [...sent, ...assigned]) unique.set(ticket.id, ticket);
  return [...unique.values()];
}

/** Kanban board of incident tickets; `/tickets/:id` opens one in a centered dialog. */
export function TicketsPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const api = useInquiriesApi();
  const auth = useAuth();
  const userId = auth.status === "signed-in" ? auth.user.id : "";
  const toasts = useToasts();
  const [tickets, setTickets] = useState<InquirySummary[] | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const reload = useCallback(async () => {
    try {
      setTickets(await loadBoard(api));
      setError("");
    } catch (cause) {
      setTickets((current) => current ?? []);
      setError(
        cause instanceof InquiryError
          ? cause.message
          : "Không tải được danh sách ticket.",
      );
    }
  }, [api]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const columns = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("vi");
    const shown = (tickets ?? []).filter(
      (ticket) =>
        (filter === "all" ||
          (filter === "mine"
            ? ticket.requested_by === userId
            : ticket.assigned_to === userId)) &&
        (!needle || ticket.title.toLocaleLowerCase("vi").includes(needle)),
    );
    shown.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    return STAGES.map((stage) => ({
      ...stage,
      tickets: shown.filter((ticket) => ticket.stage === stage.id),
    }));
  }, [tickets, filter, query, userId]);
  const shownCount = columns.reduce(
    (total, column) => total + column.tickets.length,
    0,
  );

  return (
    <main className="ticket-page" data-testid="tickets-page">
      <header className="ticket-page-heading">
        <h1>Ticket sự cố</h1>
        <p>Tạo ticket từ trò chuyện, xem đề xuất AI rồi gửi chuyên gia duyệt.</p>
      </header>
      <div className="ticket-toolbar">
        <label className="ticket-search">
          <Search size={15} aria-hidden="true" />
          <Input
            type="search"
            aria-label="Tìm ticket"
            placeholder="Tìm ticket…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div role="group" aria-label="Lọc ticket" className="ticket-filters">
          {FILTERS.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={filter === item.id}
              className="ticket-filter"
              onClick={() => setFilter(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {tickets !== null && (
          <span className="ticket-total" aria-live="polite">
            {shownCount} / {tickets.length} ticket
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {tickets === null ? (
        <p className="field-note" aria-busy="true">
          Đang tải…
        </p>
      ) : (
        <div className="ticket-board" data-testid="ticket-board">
          {columns.map((column) => (
            <section
              key={column.id}
              className="ticket-column"
              data-stage={column.id}
              data-testid={`ticket-column-${column.id}`}
              aria-label={column.label}
            >
              <header className="ticket-column-head">
                <h2>
                  <span className="ticket-dot" aria-hidden="true" />
                  {column.label}
                  <span className="ticket-count">{column.tickets.length}</span>
                </h2>
                <p>{column.hint}</p>
              </header>
              {column.tickets.length ? (
                <ul>
                  {column.tickets.map((ticket) => (
                    <TicketCard
                      key={ticket.id}
                      ticket={ticket}
                      userId={userId}
                      active={ticket.id === id}
                    />
                  ))}
                </ul>
              ) : (
                <p className="ticket-empty">{column.empty}</p>
              )}
            </section>
          ))}
        </div>
      )}
      <TicketDialog
        ticketId={id ?? null}
        userId={userId}
        onClose={() => void navigate("/tickets")}
        onChanged={() => void reload()}
        notify={toasts.show}
      />
      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </main>
  );
}
