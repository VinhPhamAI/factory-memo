import { TICKET_STATUS_LABELS } from "@/services/tickets";

/** Small coloured status label of a ticket, coloured by `data-status`. */
export function StatusLozenge({ status }: { status: string }) {
  return (
    <span className="ticket-lozenge" data-status={status}>
      {TICKET_STATUS_LABELS[status] ?? status}
    </span>
  );
}
