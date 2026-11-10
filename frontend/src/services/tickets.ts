/** Board columns of a ticket (an inquiry), as sent in the backend `stage` field. */
export type TicketStage = "todo" | "in_progress" | "in_review" | "done";

export const STAGES: {
  id: TicketStage;
  label: string;
  /** What the column means in the ticket flow, shown under its label. */
  hint: string;
  empty: string;
}[] = [
  {
    id: "todo",
    label: "To do",
    hint: "Mới tạo",
    empty: "Không có ticket mới.",
  },
  {
    id: "in_progress",
    label: "In progress",
    hint: "Có đề xuất AI",
    empty: "Chưa có đề xuất nào.",
  },
  {
    id: "in_review",
    label: "In review",
    hint: "Chờ chuyên gia",
    empty: "Không có ticket chờ.",
  },
  {
    id: "done",
    label: "Done",
    hint: "Hoàn tất",
    empty: "Chưa có ticket hoàn tất.",
  },
];

/** Ticket status labels; the board keeps statuses in English like Jira. */
export const TICKET_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  open: "Waiting for expert",
  needs_info: "Needs info",
  answered: "Approved",
  closed: "Rejected",
};

/**
 * Marks the demo AI proposal inside `context.proposed_fix`. The backend demo
 * endpoint writes the same marker; text before it is the user's own fix.
 */
export const PROPOSAL_MARKER = "[Đề xuất AI – demo]";

/** Split a proposed fix into the user's own text and the demo AI proposal. */
export function splitProposal(proposedFix: string): {
  own: string;
  proposal: string | null;
} {
  const at = proposedFix.indexOf(PROPOSAL_MARKER);
  if (at < 0) return { own: proposedFix, proposal: null };
  return {
    own: proposedFix.slice(0, at).trim(),
    proposal: proposedFix.slice(at + PROPOSAL_MARKER.length).trim(),
  };
}

/**
 * Join the user's own fix and the demo AI proposal back into one
 * `proposed_fix`, in the layout the backend demo endpoint writes.
 */
export function joinProposal(own: string, proposal: string | null): string {
  if (proposal === null) return own;
  const block = `${PROPOSAL_MARKER}\n${proposal}`;
  return own ? `${own}\n\n${block}` : block;
}

/**
 * Short display key of a ticket (not stored). Ticket IDs are time-ordered
 * UUIDs, so tickets created close together share their first characters; the
 * key uses the last six, which are random.
 */
export function ticketKey(id: string): string {
  return `TK-${id.replace(/-/g, "").slice(-6).toUpperCase()}`;
}

const RELATIVE = new Intl.RelativeTimeFormat("vi", { numeric: "auto" });

/**
 * Format a timestamp as time since `now` ("5 phút trước", "hôm qua"); dates
 * older than a week are shown as a calendar date.
 */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const minutes = Math.round((new Date(iso).getTime() - now) / 60_000);
  if (Math.abs(minutes) < 1) return "vừa xong";
  if (Math.abs(minutes) < 60) return RELATIVE.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return RELATIVE.format(hours, "hour");
  const days = Math.round(hours / 24);
  if (Math.abs(days) <= 7) return RELATIVE.format(days, "day");
  return new Date(iso).toLocaleDateString("vi-VN");
}
