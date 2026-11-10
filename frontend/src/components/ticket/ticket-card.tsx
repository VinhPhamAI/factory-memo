import type { CSSProperties } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Loader2, TicketCheck } from "lucide-react";
import { Avatar } from "@/components/ticket/avatar";
import { StatusLozenge } from "@/components/ticket/status-lozenge";
import type { InquirySummary } from "@/services/inquiries-api";
import { ticketKey, timeAgo } from "@/services/tickets";

/**
 * One ticket on the board; the whole card opens the ticket dialog. A draft of
 * the signed-in user can be dragged (`draggable`); while the AI proposal is
 * created (`analyzing`) the card is dimmed and does not open.
 */
export function TicketCard({
  ticket,
  userId,
  active,
  moved,
  order,
  draggable = false,
  dragging = false,
  analyzing = false,
  onDragStart,
  onDragEnd,
}: {
  ticket: InquirySummary;
  userId: string;
  active: boolean;
  /** The ticket just changed column; the card is highlighted briefly. */
  moved: boolean;
  /** Position in its column, used to stagger the entrance animation. */
  order: number;
  draggable?: boolean;
  /** This card is being dragged right now. */
  dragging?: boolean;
  /** The demo AI proposal is being created for this ticket. */
  analyzing?: boolean;
  onDragStart?: () => void;
  onDragEnd?: () => void;
}) {
  const mine = ticket.requested_by === userId;
  const requester = mine ? "Tôi" : ticket.requester_name;
  return (
    <li
      className="ticket-card"
      data-testid="ticket-card"
      data-active={active || undefined}
      data-moved={moved || undefined}
      data-draggable={draggable || undefined}
      data-dragging={dragging || undefined}
      data-analyzing={analyzing || undefined}
      aria-busy={analyzing || undefined}
      draggable={draggable}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", ticket.id);
        onDragStart?.();
      }}
      onDragEnd={onDragEnd}
      style={{ "--order": Math.min(order, 8) } as CSSProperties}
    >
      {/* The link covers the card; its name stays the title alone. */}
      <Link
        to={`/tickets/${ticket.id}`}
        className="ticket-card-link"
        draggable={false}
        tabIndex={analyzing ? -1 : undefined}
      >
        {ticket.title || "(Chưa có tiêu đề)"}
      </Link>
      {/* A draft's status adds nothing to its column, so only later statuses show. */}
      {analyzing ? (
        <span className="ticket-ai-chip" data-state="working" role="status">
          <Loader2 size={12} className="animate-spin" aria-hidden="true" />
          Đang phân tích…
        </span>
      ) : ticket.status !== "draft" ? (
        <StatusLozenge status={ticket.status} />
      ) : null}
      <div className="ticket-card-foot">
        <span className="ticket-key">
          <TicketCheck size={14} aria-hidden="true" />
          {ticketKey(ticket.id)}
          <span className="ticket-card-time">
            · {timeAgo(ticket.updated_at)}
          </span>
        </span>
        <span className="ticket-card-people">
          <Avatar
            name={ticket.requester_name}
            label={`Người gửi: ${requester}`}
          />
          {ticket.assignee_name && (
            <>
              <ArrowRight size={12} aria-hidden="true" />
              <Avatar
                name={ticket.assignee_name}
                label={`Chuyên gia: ${ticket.assignee_name}`}
              />
            </>
          )}
        </span>
      </div>
    </li>
  );
}
