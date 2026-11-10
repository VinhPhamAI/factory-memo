import { TICKET_STATUS_LABELS } from "@/services/tickets";

/**
 * Small coloured status label of a ticket, coloured by `data-status`. The key
 * remounts it when the status changes, which replays its entrance animation.
 */
export function StatusLozenge({ status }: { status: string }) {
  return (
    <span key={status} className="ticket-lozenge" data-status={status}>
      {TICKET_STATUS_LABELS[status] ?? status}
    </span>
  );
}
