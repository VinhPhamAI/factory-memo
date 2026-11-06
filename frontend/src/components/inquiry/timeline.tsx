import { EVENT_LABELS, type InquiryEvent } from "@/services/inquiries-api";

/** Events in order; content is rendered as text, never HTML. */
export function Timeline({ events }: { events: InquiryEvent[] }) {
  if (!events.length)
    return (
      <p className="field-note" data-testid="timeline-empty">
        Chưa có phản hồi.
      </p>
    );
  return (
    <ol className="inquiry-timeline" data-testid="timeline">
      {events.map((event) => (
        <li key={event.id} data-testid="timeline-event">
          <strong>{EVENT_LABELS[event.kind] ?? event.kind}</strong>{" "}
          <span className="field-note">
            {event.actor.display_name} ·{" "}
            {new Date(event.created_at).toLocaleString("vi-VN")}
          </span>
          {event.content && (
            <p style={{ whiteSpace: "pre-wrap" }}>{event.content}</p>
          )}
        </li>
      ))}
    </ol>
  );
}
