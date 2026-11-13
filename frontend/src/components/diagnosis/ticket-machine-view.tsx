import { lazy, Suspense, useMemo, useRef, useState } from "react";
import { ChevronDown, Loader2, LocateFixed, Rotate3d, TriangleAlert } from "lucide-react";
import { diagnosisVizEnabled } from "@/services/config";
import { resolveView } from "@/services/machine-library/registry";
import { resolveMachineView } from "@/services/machine-library/resolve";
import type { SceneApi } from "@/components/machine-3d/machine-scene";
import "./diagnosis.css";

const MachineScene = lazy(() => import("@/components/machine-3d/machine-scene"));

/**
 * Small 3D frame of the machine a ticket describes, inside the ticket dialog.
 *
 * The machine and suspected component come from the ticket's own text with
 * the same keyword rules as chat. Nothing renders when the feature flag is off
 * or the text names no machine. The frame starts open; collapsing it unmounts
 * the model.
 *
 * Args:
 *   text: Ticket title and description used to choose the machine and fault.
 *   enabled: Feature flag; defaults to `VITE_MACHINE_DIAGNOSIS_VIZ`.
 *   defaultOpen: Whether the 3D frame is shown before the user asks.
 */
export function TicketMachineView({
  text,
  enabled = diagnosisVizEnabled,
  defaultOpen = true,
}: {
  text: string;
  enabled?: boolean;
  defaultOpen?: boolean;
}) {
  const view = useMemo(() => (enabled ? resolveMachineView(text) : null), [enabled, text]);
  const resolved = resolveView(view);
  const [open, setOpen] = useState(defaultOpen);
  const [selected, setSelected] = useState<string | null>(resolved?.fault?.component ?? null);
  const [error, setError] = useState("");
  const scene = useRef<SceneApi | null>(null);
  if (!resolved) return null;
  const { machine, fault } = resolved;
  const suspect = fault ? machine.components.find((component) => component.id === fault.component) : undefined;
  const picked = machine.components.find((component) => component.id === selected);
  return (
    <section className="dx-ticket" aria-label="Mô hình 3D" data-testid="ticket-machine-view">
      <div className="dx-ticket-head">
        <span className="dx-tool-icon"><Rotate3d size={16} /></span>
        <div className="dx-tool-text">
          <strong>{machine.name}</strong>
          <span>{suspect && fault ? `Nghi vấn: ${suspect.label} · ${fault.issue}` : machine.model}</span>
        </div>
        <span className="dx-mock-chip" title="Mô hình tổng quát và dữ liệu mô phỏng">Mô phỏng</span>
        <button type="button" className="dx-tool-open" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          {open ? <ChevronDown size={15} /> : <Rotate3d size={15} />} {open ? "Thu gọn" : "Xem 3D"}
        </button>
      </div>
      {open && (
        <div className="dx-viewer dx-ticket-viewer">
          {error ? (
            <div className="dx-viewer-fallback" role="alert"><TriangleAlert size={22} /> {error}</div>
          ) : (
            <Suspense fallback={<div className="dx-viewer-loading"><Loader2 size={20} className="animate-spin" /> Đang tải mô hình 3D…</div>}>
              <MachineScene
                machine={machine}
                fault={fault}
                selected={selected}
                xray={!!picked?.interior}
                motion
                autoRotate={false}
                onSelect={setSelected}
                onError={setError}
                onReady={() => fault && scene.current?.focus(fault.component)}
                apiRef={scene}
              />
            </Suspense>
          )}
          {fault && (
            <div className="dx-toolbar" role="toolbar" aria-label="Điều khiển góc nhìn">
              <button type="button" title="Tập trung vào bộ phận nghi vấn" aria-label="Tập trung vào bộ phận nghi vấn" onClick={() => { setSelected(fault.component); scene.current?.focus(fault.component); }}>
                <LocateFixed size={16} />
              </button>
            </div>
          )}
          {picked && <p className="dx-hint">{picked.label}</p>}
        </div>
      )}
    </section>
  );
}
