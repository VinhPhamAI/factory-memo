import type { ReactNode } from "react";
import { Check, ChevronDown, CircleCheck, Loader2, LocateFixed, RotateCcw, Sparkles, TriangleAlert, Wrench } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { FaultScenario, MachineDefinition } from "@/services/machine-library/types";
import {
  fixNote,
  isRepaired,
  STAGE_LABELS,
  type MachineTicket,
  type RepairAction,
} from "@/services/machine-tickets";
import { timeAgo } from "@/services/tickets";

const ACTION_LABELS: Record<RepairAction, string> = {
  diagnose: "Chạy chẩn đoán AI",
  start: "Bắt đầu sửa",
  fix: "Đánh dấu đã sửa",
  verify: "Xác nhận chạy ổn",
  reopen: "Còn lỗi",
};

const ACTION_ICONS: Record<RepairAction, ReactNode> = {
  diagnose: <Sparkles size={16} />,
  start: <Wrench size={16} />,
  fix: <CircleCheck size={16} />,
  verify: <Check size={16} />,
  reopen: <RotateCcw size={16} />,
};

type Props = {
  tickets: MachineTicket[];
  ticket: MachineTicket;
  machine: MachineDefinition;
  /** The ticket's simulated fault, known to the panel even before diagnosis. */
  fault: FaultScenario | undefined;
  actions: RepairAction[];
  busy: RepairAction | null;
  notice: string;
  onOpenTicket(id: string): void;
  onLocate(component: string): void;
  onAction(action: RepairAction): void;
};

/**
 * Right side of the ticket workspace, kept short: which ticket, which device
 * is faulty (a button that flies the 3D view to it), what was reported, and
 * the next repair step.
 */
export function TicketPanel(props: Props) {
  const { ticket, machine, fault } = props;
  const device = ticket.stage !== "reported" && fault ? machine.components.find((component) => component.id === fault.component) : undefined;
  const repaired = isRepaired(ticket.stage);
  const note = repaired ? fixNote(ticket) : undefined;
  const state = !device ? "pending" : repaired ? "fixed" : "fault";
  const primary = props.actions.find((action) => action !== "reopen");
  const canReopen = props.actions.includes("reopen");

  return (
    <aside className="mt-panel" aria-label="Ticket sự cố" data-testid="machine-ticket-panel">
      <header className="mt-panel-head">
        <DropdownMenu>
          <DropdownMenuTrigger className="mt-switcher" aria-label={`Đổi ticket, đang mở ${ticket.key}`}>
            <span className="mt-key">{ticket.key}</span>
            <span className="mt-time">{timeAgo(ticket.createdAt)}</span>
            <ChevronDown size={14} aria-hidden="true" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" sideOffset={6} className="mt-switcher-menu">
            {props.tickets.map((item) => (
              <DropdownMenuItem key={item.id} className="mt-switcher-item" data-active={item.id === ticket.id || undefined} onSelect={() => props.onOpenTicket(item.id)}>
                <span className="mt-stage-dot" data-stage={item.stage} aria-hidden="true" />
                <span className="mt-switcher-text">
                  <strong>{item.title}</strong>
                  <span>{item.key} · {STAGE_LABELS[item.stage]}</span>
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <span className="mt-stage-badge" data-stage={ticket.stage}>{STAGE_LABELS[ticket.stage]}</span>
      </header>

      <div className="mt-panel-body">
        <h1 className="mt-title">{ticket.title}</h1>

        <section className="mt-device" data-state={state} aria-label="Thiết bị lỗi" data-testid="ticket-device">
          <span className="mt-device-label">
            {state === "pending" ? <Sparkles size={14} aria-hidden="true" /> : state === "fixed" ? <CircleCheck size={14} aria-hidden="true" /> : <TriangleAlert size={14} aria-hidden="true" />}
            {state === "pending" ? "Chưa xác định thiết bị lỗi" : state === "fixed" ? "Đã sửa" : "Thiết bị lỗi"}
          </span>
          {device && fault ? (
            <>
              <button type="button" className="mt-device-name" title="Xem trên mô hình" onClick={() => props.onLocate(device.id)}>
                {device.label}
                <LocateFixed size={16} aria-label="Xem trên mô hình" />
              </button>
              <p className="mt-device-issue">{fault.issue}</p>
              {note && <p className="mt-device-note">{note}</p>}
            </>
          ) : (
            <p className="mt-device-issue">Chạy chẩn đoán AI để tìm thiết bị lỗi trên mô hình.</p>
          )}
        </section>

        <section className="mt-desc" aria-label="Mô tả">
          <h2>Mô tả</h2>
          <p>{ticket.description}</p>
        </section>
      </div>

      <footer className="mt-panel-foot">
        <p className="mt-foot-note" role="status">{props.notice}</p>
        <div className="mt-foot-actions">
          {canReopen && (
            <button type="button" className="mt-button secondary" disabled={props.busy !== null} onClick={() => props.onAction("reopen")}>
              {props.busy === "reopen" ? <Loader2 size={16} className="animate-spin" /> : ACTION_ICONS.reopen}
              {ticket.stage === "closed" ? "Mở lại" : ACTION_LABELS.reopen}
            </button>
          )}
          {primary && (
            <button type="button" className="mt-button primary" data-action={primary} disabled={props.busy !== null} onClick={() => props.onAction(primary)}>
              {props.busy === primary ? <Loader2 size={16} className="animate-spin" /> : ACTION_ICONS[primary]}
              {props.busy === "diagnose" ? "Đang chẩn đoán…" : ACTION_LABELS[primary]}
            </button>
          )}
        </div>
      </footer>
    </aside>
  );
}
