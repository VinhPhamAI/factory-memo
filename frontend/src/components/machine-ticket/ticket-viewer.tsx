import { lazy, Suspense, type RefObject } from "react";
import {
  Bot,
  Cpu,
  Loader2,
  LocateFixed,
  Pause,
  Play,
  RotateCcw,
  ScanEye,
  TriangleAlert,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import { componentStatus } from "@/services/machine-library/registry";
import { KIND_LABELS, type ComponentSpec, type FaultScenario, type MachineDefinition, type MachineType } from "@/services/machine-library/types";
import type { SceneApi } from "@/components/machine-3d/machine-scene";

const MachineScene = lazy(() => import("@/components/machine-3d/machine-scene"));

const MACHINE_ICONS: Record<MachineType, LucideIcon> = { cnc: Cpu, conveyor: Workflow, "robot-cell": Bot };

const STATUS_TEXT = { fault: "Nghi vấn", related: "Liên quan", normal: "Bình thường" } as const;

const RUN_STEPS = ["Đọc mô tả và bằng chứng của ticket", "So sánh tín hiệu với ngưỡng vận hành", "Khoanh vùng bộ phận nghi vấn"];

export type CompareMode = "before" | "after";

type Props = {
  machine: MachineDefinition;
  location: string;
  /** Fault drawn on the model; undefined shows the machine running normally. */
  fault?: FaultScenario;
  selected: ComponentSpec | undefined;
  xray: boolean;
  motion: boolean;
  /** Before/after switch, shown once the repair is marked done. */
  compare: CompareMode | null;
  diagnosing: boolean;
  error: string;
  apiRef: RefObject<SceneApi | null>;
  onSelect(id: string | null, focus?: boolean): void;
  onCompare(mode: CompareMode): void;
  onXray(on: boolean): void;
  onMotion(on: boolean): void;
  onError(message: string): void;
};

/**
 * Left side of the ticket workspace: the 3D model of the ticket's machine with
 * a compact overlay for the machine, the selected component, a legend and a
 * before/after switch. Everything else about the ticket lives in the side panel.
 */
export function TicketViewer(props: Props) {
  const { machine, fault, selected, compare, diagnosing, apiRef } = props;
  const Icon = MACHINE_ICONS[machine.type];
  const status = selected ? componentStatus(fault, selected.id) : "normal";
  return (
    <section className="mt-stage" aria-label={`Mô hình 3D · ${machine.name}`}>
      <div className="mt-viewer" data-testid="ticket-viewer">
        {props.error ? (
          <div className="dx-viewer-fallback" role="alert"><TriangleAlert size={22} /> {props.error}</div>
        ) : (
          <Suspense fallback={<div className="dx-viewer-loading"><Loader2 size={20} className="animate-spin" /> Đang tải mô hình 3D…</div>}>
            <MachineScene
              machine={machine}
              fault={fault}
              selected={selected?.id ?? null}
              xray={props.xray}
              motion={props.motion}
              autoRotate={false}
              onSelect={(id) => props.onSelect(id, false)}
              onError={props.onError}
              apiRef={apiRef}
            />
          </Suspense>
        )}
      </div>

      <div className="mt-overlay mt-machine-chip">
        <span className="mt-machine-icon"><Icon size={17} aria-hidden="true" /></span>
        <span>
          <strong>{machine.name}</strong>
          <small>{props.location}</small>
        </span>
      </div>

      <div className="mt-overlay dx-toolbar mt-toolbar" role="toolbar" aria-label="Điều khiển góc nhìn">
        <button type="button" className={props.xray ? "active" : ""} aria-pressed={props.xray} title="Nhìn xuyên vỏ" aria-label="Nhìn xuyên vỏ" onClick={() => props.onXray(!props.xray)}><ScanEye size={16} /></button>
        <button type="button" className={props.motion ? "" : "active"} aria-pressed={!props.motion} title={props.motion ? "Tạm dừng chuyển động" : "Chạy chuyển động"} aria-label={props.motion ? "Tạm dừng chuyển động" : "Chạy chuyển động"} onClick={() => props.onMotion(!props.motion)}>{props.motion ? <Pause size={16} /> : <Play size={16} />}</button>
        {fault && <button type="button" title="Tập trung vào bộ phận nghi vấn" aria-label="Tập trung vào bộ phận nghi vấn" onClick={() => props.onSelect(fault.component)}><LocateFixed size={16} /></button>}
        <button type="button" title="Đặt lại góc nhìn" aria-label="Đặt lại góc nhìn" onClick={() => apiRef.current?.resetView()}><RotateCcw size={16} /></button>
      </div>

      {compare && (
        <div className="mt-overlay mt-compare" role="radiogroup" aria-label="So sánh trạng thái máy">
          {(["before", "after"] as const).map((mode) => (
            <button key={mode} type="button" role="radio" aria-checked={compare === mode} className={compare === mode ? `active ${mode}` : ""} onClick={() => props.onCompare(mode)}>
              <span className="mt-compare-dot" aria-hidden="true" />
              {mode === "before" ? "Khi báo lỗi" : "Sau khi sửa"}
            </button>
          ))}
        </div>
      )}

      <div className="mt-overlay mt-bottom">
        {selected ? (
          <div className={`mt-selection ${status}`} data-testid="ticket-viewer-selection">
            <span className="mt-selection-dot" aria-hidden="true" />
            <div className="mt-selection-text">
              <strong>{selected.label}</strong>
              <small>{KIND_LABELS[selected.kind]}{selected.spec ? ` · ${selected.spec}` : ""} · {STATUS_TEXT[status]}</small>
            </div>
            <button type="button" className="mt-selection-close" aria-label="Bỏ chọn bộ phận" onClick={() => props.onSelect(null, false)}><X size={14} /></button>
          </div>
        ) : (
          <p className="mt-hint">Kéo để xoay · Cuộn để thu phóng · Nhấp vào bộ phận để xem chi tiết</p>
        )}
        <ul className="mt-legend" aria-label="Chú giải màu">
          {fault ? (
            <>
              <li className="fault">Nghi vấn</li>
              {fault.related.length > 0 && <li className="related">Liên quan</li>}
            </>
          ) : (
            <li className={compare ? "normal" : "pending"}>{compare ? "Sau khi sửa · vận hành bình thường" : "Chưa khoanh vùng lỗi"}</li>
          )}
        </ul>
      </div>

      {diagnosing && (
        <div className="dx-running" role="status" data-testid="ticket-diagnosing">
          <div className="dx-scan" />
          <div className="dx-running-card">
            <Loader2 size={18} className="animate-spin" />
            <div>
              <strong>Đang chẩn đoán máy…</strong>
              <ol>{RUN_STEPS.map((step) => <li key={step}>{step}</li>)}</ol>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
