/**
 * Mock repair tickets linked to the 3D machine library.
 *
 * The `/machines/tickets` demo reads tickets through `MachineTicketsApi`, so a
 * backend endpoint can replace `createMockMachineTicketsApi` later without
 * changing the page. Tickets are synthetic and live in memory only; a reload
 * restores the fixtures.
 */
import { MACHINE_TICKET_FIXTURES } from "@/fixtures/machine-tickets";
import type { MachineView } from "@/services/machine-library/types";

/** Repair flow of a machine ticket, in order. */
export type RepairStage = "reported" | "diagnosed" | "repairing" | "verifying" | "closed";

export type RepairEventKind = "reported" | "diagnosed" | "started" | "fixed" | "verified" | "reopened";

export type RepairEvent = {
  id: string;
  kind: RepairEventKind;
  actor: string;
  at: string;
  text?: string;
};

export type MachineTicket = {
  id: string;
  /** Display key, e.g. "TK-2481". */
  key: string;
  title: string;
  stage: RepairStage;
  /** Machine and simulated fault; the faulty device is shown only after diagnosis. */
  view: Required<MachineView>;
  /** Where the machine stands, e.g. "Line 2 · VMC-850 #3". */
  location: string;
  createdAt: string;
  updatedAt: string;
  /** What the reporter saw, in one or two sentences. */
  description: string;
  /** History of the ticket; the latest "fixed" event holds what was done. */
  events: RepairEvent[];
};

export type RepairAction = "diagnose" | "start" | "fix" | "verify" | "reopen";

export const STAGE_LABELS: Record<RepairStage, string> = {
  reported: "Mới tiếp nhận",
  diagnosed: "Đã chẩn đoán",
  repairing: "Đang sửa",
  verifying: "Chờ xác nhận",
  closed: "Hoàn tất",
};

const TRANSITIONS: Record<RepairAction, { from: RepairStage[]; to: RepairStage; event: RepairEventKind }> = {
  diagnose: { from: ["reported"], to: "diagnosed", event: "diagnosed" },
  start: { from: ["diagnosed"], to: "repairing", event: "started" },
  fix: { from: ["repairing"], to: "verifying", event: "fixed" },
  verify: { from: ["verifying"], to: "closed", event: "verified" },
  reopen: { from: ["verifying", "closed"], to: "repairing", event: "reopened" },
};

/** Actions allowed at a stage; the first is the primary next step. */
export function actionsFor(stage: RepairStage): RepairAction[] {
  return (Object.keys(TRANSITIONS) as RepairAction[]).filter((action) => TRANSITIONS[action].from.includes(stage));
}

/** Whether the 3D view shows the fault: from diagnosis until the repair is marked done. */
export function showsFault(stage: RepairStage): boolean {
  return stage === "diagnosed" || stage === "repairing";
}

/** Whether the repair is done, so the view can compare before and after. */
export function isRepaired(stage: RepairStage): boolean {
  return stage === "verifying" || stage === "closed";
}

/** Text of the latest "fixed" event: what the technician did. */
export function fixNote(ticket: MachineTicket): string | undefined {
  return [...ticket.events].reverse().find((event) => event.kind === "fixed")?.text;
}

export class TicketActionError extends Error {}

/**
 * Apply a repair action to a ticket and record it in the history.
 *
 * Args:
 *   ticket: Current ticket; it is not modified.
 *   action: Step to take; it must be allowed at the ticket's stage.
 *   actor: Display name recorded on the event.
 *   now: Event time as an ISO string.
 *   note: Optional text kept on the event.
 *
 * Returns:
 *   A new ticket at the next stage with one more event.
 *
 * Raises:
 *   TicketActionError: The action is not allowed at the current stage.
 */
export function applyAction(ticket: MachineTicket, action: RepairAction, actor: string, now: string, note?: string): MachineTicket {
  const step = TRANSITIONS[action];
  if (!step.from.includes(ticket.stage))
    throw new TicketActionError(`Không thể "${action}" khi ticket đang ở trạng thái ${STAGE_LABELS[ticket.stage]}.`);
  const event: RepairEvent = { id: `${ticket.id}-e${ticket.events.length + 1}`, kind: step.event, actor, at: now, ...(note ? { text: note } : {}) };
  return { ...ticket, stage: step.to, updatedAt: now, events: [...ticket.events, event] };
}

/** Ticket data source of the demo page; a backend client can implement it later. */
export interface MachineTicketsApi {
  list(): Promise<MachineTicket[]>;
  get(id: string): Promise<MachineTicket>;
  act(id: string, action: RepairAction, note?: string): Promise<MachineTicket>;
}

/** Name recorded on actions taken in the demo. */
export const DEMO_TECHNICIAN = "Kỹ thuật viên (demo)";

/**
 * In-memory implementation of `MachineTicketsApi` over the synthetic fixtures.
 *
 * Args:
 *   options.latencyMs: Delay of each call, so loading states show in the demo.
 *   options.now: Clock used for fixtures and new events.
 *
 * Returns:
 *   An API whose changes last until the page reloads.
 */
export function createMockMachineTicketsApi({ latencyMs = 220, now = () => new Date() }: { latencyMs?: number; now?: () => Date } = {}): MachineTicketsApi {
  const store = new Map(MACHINE_TICKET_FIXTURES(now()).map((ticket) => [ticket.id, ticket]));
  const wait = <T,>(value: T) => new Promise<T>((resolve) => setTimeout(() => resolve(structuredClone(value)), latencyMs));
  const find = (id: string) => {
    const ticket = store.get(id);
    if (!ticket) throw new TicketActionError("Không tìm thấy ticket.");
    return ticket;
  };
  return {
    list: () => wait([...store.values()]),
    get: async (id) => wait(find(id)),
    act: async (id, action, note) => {
      const next = applyAction(find(id), action, action === "diagnose" ? "Trợ lý AI" : DEMO_TECHNICIAN, now().toISOString(), note);
      store.set(id, next);
      return wait(next);
    },
  };
}
