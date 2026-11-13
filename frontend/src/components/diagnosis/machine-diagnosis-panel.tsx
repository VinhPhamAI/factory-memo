import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from "react";
import {
  Activity,
  Bot,
  ChevronRight,
  CircleCheck,
  Eye,
  EyeOff,
  Cpu,
  Factory,
  Gauge,
  Loader2,
  LocateFixed,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  Rotate3d,
  RotateCcw,
  ScanEye,
  Stethoscope,
  Thermometer,
  Timer,
  TriangleAlert,
  Waves,
  Wind,
  Workflow,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import type { DiagnosisState } from "@/hooks/machine-diagnosis";
import {
  componentStatus,
  listMachines,
  listScenarios,
  resolveView,
  selectableComponents,
} from "@/services/machine-library/registry";
import {
  COMPONENT_GROUPS,
  KIND_LABELS,
  type ComponentSpec,
  type FaultScenario,
  type MachineDefinition,
  type MachineType,
  type MachineView,
  type Metric,
} from "@/services/machine-library/types";
import type { SceneApi } from "@/components/machine-3d/machine-scene";
import type { ViewerStats } from "@/components/machine-3d/viewer";
import "./diagnosis.css";

const MachineScene = lazy(() => import("@/components/machine-3d/machine-scene"));

const METRIC_ICONS: Record<Metric["icon"], LucideIcon> = {
  temperature: Thermometer,
  vibration: Waves,
  cycle: Timer,
  rate: Gauge,
  pressure: Wind,
  current: Zap,
};

const MACHINE_ICONS: Record<MachineType, LucideIcon> = { cnc: Cpu, conveyor: Workflow, "robot-cell": Bot };
const MACHINE_SHORT: Record<MachineType, string> = { cnc: "Máy CNC", conveyor: "Băng tải", "robot-cell": "Robot cell" };

const RUN_STEPS = ["Đọc tín hiệu máy", "So sánh với ngưỡng vận hành", "Xếp hạng bộ phận nghi vấn"];

/** Width at which the docked panel floats above the chat instead of beside it. */
const OVERLAY_QUERY = "(max-width: 1100px)";

/** Report whether a media query matches and follow its changes. */
function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      const list = window.matchMedia?.(query);
      list?.addEventListener("change", notify);
      return () => list?.removeEventListener("change", notify);
    },
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  );
}

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Report whether a diagnosis overlay currently covers the chat.
 *
 * Chat pages use this to avoid moving focus behind an open overlay.
 */
export function diagnosisOverlayOpen(): boolean {
  return !!document.querySelector('[data-testid="diagnosis-panel"][aria-modal="true"]');
}

/** Focusable elements inside a container, skipping inert regions. */
function focusableIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((element) => !element.closest("[inert]"));
}

/** Format a metric the way the panel shows it, e.g. "+18 %" for a change. */
export function formatMetric(metric: Metric): string {
  return `${metric.sign ?? ""}${metric.value} ${metric.unit}`;
}

/** One symptom reading with its normal range and a bar against the limit. */
export function MetricRow({ metric }: { metric: Metric }) {
  const Icon = METRIC_ICONS[metric.icon];
  const ratio = Math.min(metric.value / metric.limit, 2) / 2;
  return (
    <li className="dx-metric" data-testid="diagnosis-metric">
      <span className="dx-metric-icon"><Icon size={18} aria-hidden="true" /></span>
      <span className="dx-metric-label">{metric.label}</span>
      <span className="dx-metric-value">
        <strong>{formatMetric(metric)}</strong>
        <small>{metric.normal}</small>
      </span>
      <span className="dx-metric-bar" aria-hidden="true">
        <span className="dx-metric-limit" />
        <span className="dx-metric-fill" style={{ width: `${Math.max(ratio * 100, 6)}%` }} />
      </span>
    </li>
  );
}

function Checklist({ fault, machine, onLocate }: { fault: FaultScenario; machine: MachineDefinition; onLocate(id: string): void }) {
  const [done, setDone] = useState<boolean[]>(() => fault.checks.map(() => false));
  const count = done.filter(Boolean).length;
  const labelOf = (id: string) => machine.components.find((component) => component.id === id)?.label ?? id;
  return (
    <section className="dx-section">
      <header className="dx-section-head">
        <h3>Hạng mục kiểm tra đề xuất</h3>
        <span className="dx-count">{count}/{fault.checks.length}</span>
      </header>
      <div className="dx-progress" aria-hidden="true"><span style={{ width: `${(count / fault.checks.length) * 100}%` }} /></div>
      <ul className="dx-checks">
        {fault.checks.map((check, index) => (
          <li key={check.text}>
            <label className={done[index] ? "done" : ""}>
              <input
                type="checkbox"
                checked={done[index]}
                onChange={() => setDone((items) => items.map((value, i) => (i === index ? !value : value)))}
              />
              <span className="dx-check-box"><CircleCheck size={14} aria-hidden="true" /></span>
              <span>{check.text}</span>
            </label>
            {check.component && (
              <button
                type="button"
                className="dx-locate"
                title={`Xem ${labelOf(check.component)} trên mô hình`}
                aria-label={`Xem ${labelOf(check.component)} trên mô hình`}
                onClick={() => onLocate(check.component!)}
              >
                <LocateFixed size={15} />
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function DetailedRun() {
  const [state, setState] = useState<"idle" | "running" | "done">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <footer className="dx-footer">
      {state === "done" && (
        <p className="dx-footer-note" role="status">
          Bản mock chưa kết nối dịch vụ chẩn đoán chi tiết. Kết quả trên là dữ liệu mô phỏng.
        </p>
      )}
      <button
        type="button"
        className="dx-primary"
        disabled={state === "running"}
        onClick={() => {
          setState("running");
          timer.current = setTimeout(() => setState("done"), 1400);
        }}
      >
        {state === "running" ? <Loader2 size={18} className="animate-spin" /> : <Stethoscope size={18} />}
        <span>{state === "running" ? "Đang phân tích chi tiết…" : "Chạy chẩn đoán chi tiết"}</span>
        <ChevronRight size={18} />
      </button>
    </footer>
  );
}

const HIDDEN_KEY = "machine-3d:hidden";
const EMPTY: string[] = [];

/** Read the components each machine hides, saved in this browser only. */
function loadHidden(): Record<string, string[]> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? "{}");
    if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
    return Object.fromEntries(
      Object.entries(value).filter((entry): entry is [string, string[]] => Array.isArray(entry[1]) && entry[1].every((id) => typeof id === "string")),
    );
  } catch {
    return {};
  }
}

function saveHidden(hidden: Record<string, string[]>) {
  try {
    localStorage.setItem(HIDDEN_KEY, JSON.stringify(hidden));
  } catch {
    // Private windows or blocked storage: hiding still works for this visit.
  }
}

/** Components of a machine grouped for the picker, in group order. */
function grouped(machine: MachineDefinition) {
  const items = selectableComponents(machine);
  return COMPONENT_GROUPS.map((group) => ({ ...group, items: items.filter((item) => item.group === group.id) })).filter((group) => group.items.length);
}

function SelectionCard({
  component,
  machine,
  fault,
  onShowSuspect,
}: {
  component: ComponentSpec;
  machine: MachineDefinition;
  fault?: FaultScenario;
  onShowSuspect(): void;
}) {
  const status = componentStatus(fault, component.id);
  const suspect = fault ? machine.components.find((item) => item.id === fault.component) : undefined;
  const kind = `${KIND_LABELS[component.kind]}${component.spec ? ` · ${component.spec}` : ""}`;
  if (fault && status === "fault")
    return (
      <section className="dx-alert" data-testid="diagnosis-suspect">
        <span className="dx-alert-icon"><TriangleAlert size={22} aria-hidden="true" /></span>
        <div>
          <span className="dx-alert-kicker">Nghi vấn (tự động)</span>
          <h3>{component.label}</h3>
          <p className="dx-kind">{kind}</p>
          <p>Vấn đề có thể: <strong>{fault.issue}</strong></p>
          <p className="dx-alert-summary">{fault.summary}</p>
          <p className="dx-caveat">Giả thuyết từ dữ liệu mô phỏng; cần xác nhận tại máy trước khi xử lý.</p>
        </div>
      </section>
    );
  if (fault && status === "related")
    return (
      <section className="dx-related" data-testid="diagnosis-related">
        <span className="dx-related-icon"><Activity size={20} aria-hidden="true" /></span>
        <div>
          <span className="dx-alert-kicker">Liên quan</span>
          <h3>{component.label}</h3>
          <p className="dx-kind">{kind}</p>
          <p>Có thể góp phần vào: <strong>{fault.issue}</strong>. Kiểm tra cùng bộ phận nghi vấn.</p>
          {suspect && (
            <button type="button" className="dx-link" onClick={onShowSuspect}>
              Xem bộ phận nghi vấn: {suspect.label} <ChevronRight size={14} />
            </button>
          )}
        </div>
      </section>
    );
  return (
    <section className="dx-ok" data-testid="diagnosis-component-ok">
      <span className="dx-ok-icon"><CircleCheck size={20} aria-hidden="true" /></span>
      <div>
        <h3>{component.label}</h3>
        <p className="dx-kind">{kind}</p>
        <p>{fault ? "Không có dấu hiệu bất thường trong dữ liệu mô phỏng." : "Chưa có kịch bản lỗi cho máy này."}</p>
        {suspect && (
          <button type="button" className="dx-link" onClick={onShowSuspect}>
            Xem bộ phận nghi vấn: {suspect.label} <ChevronRight size={14} />
          </button>
        )}
      </div>
    </section>
  );
}

function StatsBadge({ stats }: { stats: ViewerStats }) {
  return (
    <div className="dx-stats" data-testid="diagnosis-stats" aria-live="off">
      <span className={stats.fps >= 55 ? "good" : stats.fps >= 30 ? "fair" : "poor"}>{stats.fps} FPS</span>
      <span>{stats.backend === "webgpu" ? "WebGPU" : "WebGL"}</span>
      <span>{stats.calls} draw</span>
      <span>{Math.round(stats.triangles / 1000)}k tris</span>
      <span>{stats.pixelRatio}×</span>
    </div>
  );
}

type PanelProps = {
  state: DiagnosisState;
  onClose(): void;
  onOpen(): void;
  /**
   * `docked` sits in the page grid; `overlay` floats above layouts with their
   * own side column; `page` fills a standalone page with no close control.
   */
  variant?: "docked" | "overlay" | "page";
};

/**
 * Right-hand diagnosis view: an interactive 3D model of the machine, the
 * suspected component, symptoms and a checklist linked to the model. When
 * closed it leaves a pill to reopen it.
 */
export function MachineDiagnosisPanel({ state, onClose, onOpen, variant = "docked" }: PanelProps) {
  const { open, phase } = state;
  const resolved = resolveView(state.view);
  const [view, setView] = useState<MachineView | null>(state.view);
  const [lastView, setLastView] = useState(state.view);
  const current = resolveView(view) ?? resolved;
  const machine = current?.machine;
  const fault = current?.fault;
  const [selected, setSelected] = useState<string | null>(fault?.component ?? null);
  const [autoRotate, setAutoRotate] = useState(false);
  const [motion, setMotion] = useState(() => !(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false));
  const [xrayChoice, setXrayChoice] = useState<boolean | null>(null);
  const [showStats, setShowStats] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [stats, setStats] = useState<ViewerStats | null>(null);
  const [hiddenByMachine, setHiddenByMachine] = useState<Record<string, string[]>>(loadHidden);
  const [sceneError, setSceneError] = useState("");
  const scene = useRef<SceneApi | null>(null);
  const panel = useRef<HTMLElement>(null);
  const narrow = useMediaQuery(OVERLAY_QUERY);
  // An overlay covers the chat, so it holds focus like a dialog until closed.
  const page = variant === "page";
  const modal = !page && (variant === "overlay" || narrow || expanded);
  const visible = !!resolved && open;
  if (state.view !== lastView) {
    // A new diagnosis replaces any machine the user switched to.
    setLastView(state.view);
    setView(state.view);
    setSelected(resolveView(state.view)?.fault?.component ?? null);
    setXrayChoice(null);
  }
  const selectedSpec = machine?.components.find((component) => component.id === selected);
  // Interior parts are hidden by guards; x-ray turns on for them unless the user chose otherwise.
  const xray = xrayChoice ?? !!selectedSpec?.interior;
  const groups = useMemo(() => (machine ? grouped(machine) : []), [machine]);

  // The element focused before the overlay took focus, restored on close.
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!visible || !modal || !panel.current) return;
    const active = document.activeElement;
    if (panel.current.contains(active)) return;
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null;
    panel.current.querySelector<HTMLElement>('[aria-label="Đóng chẩn đoán"]')?.focus();
  }, [visible, modal]);

  useEffect(() => {
    if (!visible) return;
    return () => {
      // Closing returns focus to the opener, or to the reopen button.
      const target = opener.current;
      opener.current = null;
      if (target?.isConnected) target.focus();
      else requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-testid="diagnosis-reopen"]')?.focus());
    };
  }, [visible]);

  if (!resolved || !machine) return null;
  if (!open)
    return (
      <button type="button" className="dx-reopen" onClick={onOpen} data-testid="diagnosis-reopen">
        <span className={`dx-reopen-dot ${resolved.fault ? "" : "ok"}`} />
        <Rotate3d size={16} aria-hidden="true" /> Mô hình 3D · {resolved.machine.name}
      </button>
    );

  const ready = phase === "ready";
  const hidden = hiddenByMachine[machine.id] ?? EMPTY;
  const toggleHidden = (id: string) => {
    const next = hidden.includes(id) ? hidden.filter((item) => item !== id) : [...hidden, id];
    const all = { ...hiddenByMachine, [machine.id]: next };
    if (!next.length) delete all[machine.id];
    setHiddenByMachine(all);
    saveHidden(all);
  };
  const showAll = () => {
    const all = { ...hiddenByMachine };
    delete all[machine.id];
    setHiddenByMachine(all);
    saveHidden(all);
  };
  const select = (id: string, focus = true) => {
    setSelected(id);
    if (focus) scene.current?.focus(id);
  };
  const switchMachine = (id: string) => {
    if (id === machine.id) return;
    const keep = resolved.machine.id === id ? state.view : { machine: id };
    setView(keep);
    setSelected(resolveView(keep)?.fault?.component ?? null);
    setXrayChoice(null);
    setStats(null);
  };
  const simulate = (scenario: FaultScenario | null) => {
    setView({ machine: machine.id, scenario: scenario?.id });
    setSelected(scenario?.component ?? null);
    if (scenario) scene.current?.focus(scenario.component);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape" && !page) onClose();
    if (event.key !== "Tab" || !modal || !panel.current) return;
    // Keep keyboard focus inside the overlay while it covers the chat.
    const items = focusableIn(panel.current);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    // The panel itself holds focus after a click on non-interactive content.
    if (document.activeElement === panel.current) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  const scenarios = listScenarios(machine.id);

  return (
    <aside
      ref={panel}
      className={`dx-panel ${variant} ${expanded ? "expanded" : ""}`}
      aria-label="Chẩn đoán máy"
      role={modal ? "dialog" : undefined}
      aria-modal={modal ? true : undefined}
      tabIndex={modal ? -1 : undefined}
      data-testid="diagnosis-panel"
      onKeyDown={onKeyDown}
    >
      <header className="dx-head">
        <div className="dx-head-text">
          <span className="dx-kicker"><Activity size={13} aria-hidden="true" /> Chẩn đoán máy · 3D</span>
          <h2><Factory size={18} aria-hidden="true" /> {machine.name}</h2>
          <p className="dx-model">{machine.model}</p>
        </div>
        <span className="dx-mock-chip" title="Mô hình tổng quát và dữ liệu mô phỏng, không phải số đo thực tế">Dữ liệu mô phỏng</span>
        {!page && (<>
        <button
          type="button"
          className="dx-icon-button dx-expand"
          aria-pressed={expanded}
          aria-label={expanded ? "Thu nhỏ khung 3D" : "Mở rộng khung 3D"}
          title={expanded ? "Thu nhỏ" : "Mở rộng"}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
        </button>
        <button type="button" className="dx-icon-button" aria-label="Đóng chẩn đoán" onClick={onClose}>
          <X size={18} />
        </button>
        </>)}
      </header>

      <div className="dx-body">
        <div className="dx-machines" role="tablist" aria-label="Chọn máy">
          {listMachines().map((item) => {
            const Icon = MACHINE_ICONS[item.type];
            const active = item.id === machine.id;
            const hasFault = (active ? fault : resolved.machine.id === item.id ? resolved.fault : undefined) !== undefined;
            return (
              <button key={item.id} type="button" role="tab" aria-selected={active} className={active ? "active" : ""} onClick={() => switchMachine(item.id)}>
                <Icon size={15} aria-hidden="true" />
                <span>{MACHINE_SHORT[item.type]}</span>
                {hasFault && <span className="dx-tab-dot" aria-label="có nghi vấn" />}
              </button>
            );
          })}
        </div>

        <div className="dx-viewer">
          {sceneError ? (
            <div className="dx-viewer-fallback" role="alert"><TriangleAlert size={22} /> {sceneError}</div>
          ) : (
            <Suspense fallback={<div className="dx-viewer-loading"><Loader2 size={20} className="animate-spin" /> Đang tải mô hình 3D…</div>}>
              <MachineScene
                machine={machine}
                fault={fault}
                selected={selected}
                hidden={hidden}
                xray={xray}
                motion={motion}
                autoRotate={autoRotate}
                onSelect={(id) => select(id, false)}
                onError={setSceneError}
                onStats={showStats ? setStats : undefined}
                onReady={() => {
                  // Fly to the suspect while the mock run finishes.
                  if (fault) scene.current?.focus(fault.component);
                }}
                apiRef={scene}
              />
            </Suspense>
          )}
          <div className="dx-toolbar" role="toolbar" aria-label="Điều khiển góc nhìn" inert={!ready}>
            <button type="button" className={autoRotate ? "active" : ""} aria-pressed={autoRotate} title="Tự động xoay" aria-label="Tự động xoay" onClick={() => setAutoRotate((value) => !value)}><Rotate3d size={16} /></button>
            <button type="button" className={xray ? "active" : ""} aria-pressed={xray} title="Nhìn xuyên vỏ" aria-label="Nhìn xuyên vỏ" onClick={() => setXrayChoice(!xray)}><ScanEye size={16} /></button>
            <button type="button" className={motion ? "" : "active"} aria-pressed={!motion} title={motion ? "Tạm dừng chuyển động" : "Chạy chuyển động"} aria-label={motion ? "Tạm dừng chuyển động" : "Chạy chuyển động"} onClick={() => setMotion((value) => !value)}>{motion ? <Pause size={16} /> : <Play size={16} />}</button>
            {fault && <button type="button" title="Tập trung vào bộ phận nghi vấn" aria-label="Tập trung vào bộ phận nghi vấn" onClick={() => select(fault.component)}><LocateFixed size={16} /></button>}
            <button type="button" title="Đặt lại góc nhìn" aria-label="Đặt lại góc nhìn" onClick={() => scene.current?.resetView()}><RotateCcw size={16} /></button>
            <button type="button" className={showStats ? "active" : ""} aria-pressed={showStats} title="Hiệu năng" aria-label="Hiệu năng" onClick={() => { setShowStats((value) => !value); setStats(null); }}><Gauge size={16} /></button>
          </div>
          {showStats && stats && <StatsBadge stats={stats} />}
          <p className="dx-hint">Kéo để xoay · Cuộn để thu phóng · Nhấp đúp vào bộ phận để phóng to</p>
          {!ready && (
            <div className="dx-running" role="status" data-testid="diagnosis-running">
              <div className="dx-scan" />
              <div className="dx-running-card">
                <Loader2 size={18} className="animate-spin" />
                <div>
                  <strong>Đang chạy chẩn đoán máy…</strong>
                  <ol>{RUN_STEPS.map((step) => <li key={step}>{step}</li>)}</ol>
                </div>
              </div>
            </div>
          )}
        </div>

        <nav className="dx-parts" aria-label="Bộ phận của máy">
          {hidden.length > 0 && (
            <div className="dx-hidden-bar" role="status">
              <EyeOff size={14} aria-hidden="true" />
              <span>Đang ẩn {hidden.length} bộ phận trên mô hình</span>
              <button type="button" className="dx-link" onClick={showAll}>Hiện tất cả</button>
            </div>
          )}
          {groups.map((group) => (
            <div key={group.id} className="dx-part-group">
              <span className="dx-part-group-label">{group.label}</span>
              <div className="dx-part-list">
                {group.items.map((item) => {
                  const status = componentStatus(fault, item.id);
                  const isHidden = hidden.includes(item.id);
                  return (
                    <span key={item.id} className={`dx-part ${status} ${item.id === selected ? "selected" : ""} ${isHidden ? "is-hidden" : ""}`}>
                      <button type="button" className="dx-part-main" aria-pressed={item.id === selected} onClick={() => select(item.id, !isHidden)}>
                        <span className="dx-part-dot" aria-hidden="true" />
                        {item.label}
                      </button>
                      <button
                        type="button"
                        className="dx-part-eye"
                        aria-label={`${isHidden ? "Hiện" : "Ẩn"} ${item.label}`}
                        title={isHidden ? "Hiện lại trên mô hình" : "Ẩn khỏi mô hình"}
                        aria-pressed={isHidden}
                        onClick={() => toggleHidden(item.id)}
                      >
                        {isHidden ? <EyeOff size={13} /> : <Eye size={13} />}
                      </button>
                    </span>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Covered findings stay out of the tab order until the run ends. */}
        <div className={`dx-content ${ready ? "ready" : ""}`} inert={!ready}>
          {selectedSpec ? (
            <>
              <SelectionCard component={selectedSpec} machine={machine} fault={fault} onShowSuspect={() => fault && select(fault.component)} />
              <button type="button" className="dx-hide-toggle" onClick={() => toggleHidden(selectedSpec.id)}>
                {hidden.includes(selectedSpec.id) ? <Eye size={15} aria-hidden="true" /> : <EyeOff size={15} aria-hidden="true" />}
                {hidden.includes(selectedSpec.id) ? "Hiện lại bộ phận này trên mô hình" : "Ẩn bộ phận này khỏi mô hình"}
              </button>
            </>
          ) : (
            <section className="dx-ok" data-testid="diagnosis-machine-ok">
              <span className="dx-ok-icon"><CircleCheck size={20} aria-hidden="true" /></span>
              <div>
                <h3>{machine.name}</h3>
                <p>{machine.summary}</p>
                <p className="dx-kind">Chọn một bộ phận trên mô hình hoặc trong danh sách để xem chi tiết.</p>
              </div>
            </section>
          )}

          {fault && (
            <section className="dx-section">
              <header className="dx-section-head"><h3>Triệu chứng phát hiện</h3></header>
              <ul className="dx-metrics">{fault.metrics.map((metric) => <MetricRow key={metric.id} metric={metric} />)}</ul>
            </section>
          )}

          {fault && <Checklist key={fault.id} fault={fault} machine={machine} onLocate={select} />}

          <section className="dx-section dx-scenarios">
            <header className="dx-section-head"><h3>Kịch bản mô phỏng</h3></header>
            <div className="dx-scenario-list">
              {scenarios.map((scenario) => (
                <button key={scenario.id} type="button" className={scenario.id === fault?.id ? "active" : ""} aria-pressed={scenario.id === fault?.id} onClick={() => simulate(scenario)}>
                  {scenario.issue}
                </button>
              ))}
              <button type="button" className={!fault ? "active" : ""} aria-pressed={!fault} onClick={() => simulate(null)}>Vận hành bình thường</button>
            </div>
          </section>
        </div>
      </div>
      <DetailedRun key={`${machine.id}:${fault?.id ?? ""}`} />
    </aside>
  );
}

/** Inline chat card for the mock diagnosis "tool call" of one answer. */
export function DiagnosisToolCard({ view, running, onOpen }: { view: MachineView; running: boolean; onOpen(): void }) {
  const resolved = resolveView(view);
  if (!resolved) return null;
  const { machine, fault } = resolved;
  const suspect = fault ? machine.components.find((component) => component.id === fault.component) : undefined;
  return (
    <div className={`dx-tool-card ${running ? "running" : ""}`} data-testid="diagnosis-tool-card">
      <span className="dx-tool-icon">{running ? <Loader2 size={16} className="animate-spin" /> : <Stethoscope size={16} />}</span>
      <span className="dx-tool-text">
        <strong>Chẩn đoán máy · {machine.name}</strong>
        <span>{running ? "Đang phân tích máy…" : suspect && fault ? `${suspect.label} · ${fault.issue}` : "Mở mô hình 3D để chọn bộ phận"}</span>
      </span>
      <code>machine_diagnosis</code>
      <button type="button" className="dx-tool-open" onClick={onOpen} disabled={running}>
        <Rotate3d size={15} /> Xem 3D
      </button>
    </div>
  );
}
