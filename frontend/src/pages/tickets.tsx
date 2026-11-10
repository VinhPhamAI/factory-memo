import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

/** How long a card that changed column stays highlighted. */
const MOVED_HIGHLIGHT_MS = 2200;

/** Placeholder cards per column while the board loads. */
const SKELETON_CARDS = [2, 1, 1, 0];

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

  // Last seen column of each ticket, to highlight cards that changed column.
  const stages = useRef(new Map<string, string>());
  const [moved, setMoved] = useState<ReadonlySet<string>>(new Set());

  const reload = useCallback(async () => {
    try {
      const loaded = await loadBoard(api);
      const changed = new Set<string>();
      for (const ticket of loaded) {
        const before = stages.current.get(ticket.id);
        if (before !== undefined && before !== ticket.stage)
          changed.add(ticket.id);
        stages.current.set(ticket.id, ticket.stage);
      }
      setTickets(loaded);
      if (changed.size) setMoved(changed);
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

  useEffect(() => {
    if (!moved.size) return;
    const timer = setTimeout(() => setMoved(new Set()), MOVED_HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [moved]);

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

  return (
    <main className="ticket-page" data-testid="tickets-page">
      <header className="ticket-page-heading">
        <h1 className="page-heading">Ticket sự cố</h1>
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
      </div>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {tickets === null ? (
        <div className="ticket-board" aria-busy="true" data-testid="ticket-loading">
          <span className="sr-only">Đang tải…</span>
          {STAGES.map((stage, index) => (
            <div
              key={stage.id}
              className="ticket-column"
              data-stage={stage.id}
              aria-hidden="true"
            >
              <div className="ticket-column-head">
                <h2>
                  <span className="ticket-dot" />
                  {stage.label}
                </h2>
                <p>{stage.hint}</p>
              </div>
              <ul>
                {Array.from({ length: SKELETON_CARDS[index] }, (_, card) => (
                  <li key={card} className="ticket-card ticket-card-skeleton">
                    <span className="ticket-skeleton" data-width="full" />
                    <span className="ticket-skeleton" data-width="mid" />
                    <span className="ticket-skeleton" data-width="short" />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
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
                  {/* The key restarts the bump animation when the count changes. */}
                  <span key={column.tickets.length} className="ticket-count">
                    {column.tickets.length}
                  </span>
                </h2>
                <p>{column.hint}</p>
              </header>
              {column.tickets.length ? (
                <ul>
                  {column.tickets.map((ticket, index) => (
                    <TicketCard
                      key={ticket.id}
                      ticket={ticket}
                      userId={userId}
                      active={ticket.id === id}
                      moved={moved.has(ticket.id)}
                      order={index}
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
