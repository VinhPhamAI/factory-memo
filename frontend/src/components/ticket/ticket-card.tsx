import type { CSSProperties } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, TicketCheck } from "lucide-react";
import { Avatar } from "@/components/ticket/avatar";
import { StatusLozenge } from "@/components/ticket/status-lozenge";
import type { InquirySummary } from "@/services/inquiries-api";
import { ticketKey, timeAgo } from "@/services/tickets";

/** One ticket on the board; the whole card opens the ticket dialog. */
export function TicketCard({
  ticket,
  userId,
  active,
  moved,
  order,
}: {
  ticket: InquirySummary;
  userId: string;
  active: boolean;
  /** The ticket just changed column; the card is highlighted briefly. */
  moved: boolean;
  /** Position in its column, used to stagger the entrance animation. */
  order: number;
}) {
  const mine = ticket.requested_by === userId;
  const requester = mine ? "Tôi" : ticket.requester_name;
  return (
    <li
      className="ticket-card"
      data-testid="ticket-card"
      data-active={active || undefined}
      data-moved={moved || undefined}
      style={{ "--order": Math.min(order, 8) } as CSSProperties}
    >
      {/* The link covers the card; its name stays the title alone. */}
      <Link to={`/tickets/${ticket.id}`} className="ticket-card-link">
        {ticket.title || "(Chưa có tiêu đề)"}
      </Link>
      {/* A draft's status adds nothing to its column, so only later statuses show. */}
      {ticket.status !== "draft" && <StatusLozenge status={ticket.status} />}
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
