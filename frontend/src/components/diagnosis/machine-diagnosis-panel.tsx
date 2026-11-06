import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from "react";
import {
  Activity,
  ChevronRight,
  CircleCheck,
  Factory,
  Gauge,
  Loader2,
  LocateFixed,
  Rotate3d,
  RotateCcw,
  Stethoscope,
  Thermometer,
  Timer,
  TriangleAlert,
  Waves,
  X,
} from "lucide-react";
import type { DiagnosisState } from "@/hooks/machine-diagnosis";
import {
  formatMetric,
  STATIONS,
  stationOf,
  type DiagnosisScenario,
  type Metric,
  type StationId,
} from "@/services/machine-diagnosis";
import type { SceneApi } from "./production-line-scene";
import "./diagnosis.css";

const ProductionLineScene = lazy(() => import("./production-line-scene"));

const METRIC_ICONS = { temperature: Thermometer, vibration: Waves, cycle: Timer, rate: Gauge } as const;

const RUN_STEPS = ["Đọc tín hiệu dây chuyền", "So sánh với ngưỡng vận hành", "Xếp hạng nguyên nhân khả dĩ"];

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
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => !element.closest("[inert]"),
  );
}

function MetricRow({ metric }: { metric: Metric }) {
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

function Checklist({ scenario }: { scenario: DiagnosisScenario }) {
  const [done, setDone] = useState<boolean[]>(() => scenario.checks.map(() => false));
  const count = done.filter(Boolean).length;
  return (
    <section className="dx-section">
      <header className="dx-section-head">
        <h3>Hạng mục kiểm tra đề xuất</h3>
        <span className="dx-count">{count}/{scenario.checks.length}</span>
      </header>
      <div className="dx-progress" aria-hidden="true"><span style={{ width: `${(count / scenario.checks.length) * 100}%` }} /></div>
      <ul className="dx-checks">
        {scenario.checks.map((check, index) => (
          <li key={check}>
            <label className={done[index] ? "done" : ""}>
              <input
                type="checkbox"
                checked={done[index]}
                onChange={() => setDone((items) => items.map((value, i) => (i === index ? !value : value)))}
              />
              <span className="dx-check-box"><CircleCheck size={14} aria-hidden="true" /></span>
              <span>{check}</span>
            </label>
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

type PanelProps = {
  state: DiagnosisState;
  onClose(): void;
  onOpen(): void;
  /** `docked` sits in the page grid; `overlay` floats above layouts with their own side column. */
  variant?: "docked" | "overlay";
};

/**
 * Right-hand mock diagnosis view: an interactive 3D line plus the suspected
 * station, symptoms and checks. When closed it leaves a pill to reopen it.
 */
export function MachineDiagnosisPanel({ state, onClose, onOpen, variant = "docked" }: PanelProps) {
  const { scenario, open, phase } = state;
  const [selected, setSelected] = useState<StationId>(scenario?.station ?? "processing");
  const [autoRotate, setAutoRotate] = useState(false);
  const [sceneError, setSceneError] = useState("");
  const scene = useRef<SceneApi | null>(null);
  const panel = useRef<HTMLElement>(null);
  const narrow = useMediaQuery(OVERLAY_QUERY);
  // An overlay covers the chat, so it holds focus like a dialog until closed.
  const modal = variant === "overlay" || narrow;
  const visible = !!scenario && open;
  const [lastScenario, setLastScenario] = useState(scenario);
  if (scenario !== lastScenario) {
    // A new diagnosis selects its suspected station again.
    setLastScenario(scenario);
    if (scenario) setSelected(scenario.station);
  }

  // The element focused before the overlay took focus, restored on close.
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!visible || !modal || !panel.current) return;
    const active = document.activeElement;
    if (panel.current.contains(active)) return;
    opener.current =
      active instanceof HTMLElement && active !== document.body ? active : null;
    panel.current.querySelector<HTMLElement>(".dx-icon-button")?.focus();
  }, [visible, modal]);

  useEffect(() => {
    if (!visible) return;
    return () => {
      // Closing returns focus to the opener, or to the reopen button.
      const target = opener.current;
      opener.current = null;
      if (target?.isConnected) target.focus();
      else
        requestAnimationFrame(() =>
          document.querySelector<HTMLElement>('[data-testid="diagnosis-reopen"]')?.focus(),
        );
    };
  }, [visible]);

  if (!scenario) return null;
  if (!open)
    return (
      <button type="button" className="dx-reopen" onClick={onOpen} data-testid="diagnosis-reopen">
        <span className="dx-reopen-dot" />
        <Rotate3d size={16} aria-hidden="true" /> Mô phỏng 3D · {stationOf(scenario.station).name}
      </button>
    );

  const fault = stationOf(scenario.station);
  const station = stationOf(selected);
  const faulty = selected === scenario.station;
  const ready = phase === "ready";
  const select = (id: StationId) => {
    setSelected(id);
    scene.current?.focus(id);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") onClose();
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

  return (
    <aside
      ref={panel}
      className={`dx-panel ${variant}`}
      aria-label="Chẩn đoán máy"
      role={modal ? "dialog" : undefined}
      aria-modal={modal ? true : undefined}
      tabIndex={modal ? -1 : undefined}
      data-testid="diagnosis-panel"
      onKeyDown={onKeyDown}
    >
      <header className="dx-head">
        <div className="dx-head-text">
          <span className="dx-kicker"><Activity size={13} aria-hidden="true" /> Chẩn đoán máy</span>
          <h2><Factory size={18} aria-hidden="true" /> Dây chuyền {scenario.line}</h2>
        </div>
        <span className="dx-mock-chip" title="Dữ liệu mô phỏng, không phải số đo thực tế">Dữ liệu mô phỏng</span>
        <button type="button" className="dx-icon-button" aria-label="Đóng chẩn đoán" onClick={onClose}>
          <X size={18} />
        </button>
      </header>

      <div className="dx-body">
        <div className="dx-viewer">
          {sceneError ? (
            <div className="dx-viewer-fallback" role="alert"><TriangleAlert size={22} /> {sceneError}</div>
          ) : (
            <Suspense fallback={<div className="dx-viewer-loading"><Loader2 size={20} className="animate-spin" /> Đang tải mô hình 3D…</div>}>
              <ProductionLineScene
                scenario={scenario}
                selected={selected}
                autoRotate={autoRotate}
                onSelect={select}
                onError={setSceneError}
                apiRef={scene}
              />
            </Suspense>
          )}
          <div className="dx-toolbar" role="toolbar" aria-label="Điều khiển góc nhìn" inert={!ready}>
            <button type="button" className={autoRotate ? "active" : ""} aria-pressed={autoRotate} title="Tự động xoay" aria-label="Tự động xoay" onClick={() => setAutoRotate((value) => !value)}><Rotate3d size={16} /></button>
            <button type="button" title="Tập trung vào trạm lỗi" aria-label="Tập trung vào trạm lỗi" onClick={() => select(scenario.station)}><LocateFixed size={16} /></button>
            <button type="button" title="Đặt lại góc nhìn" aria-label="Đặt lại góc nhìn" onClick={() => scene.current?.resetView()}><RotateCcw size={16} /></button>
          </div>
          <p className="dx-hint">Kéo để xoay · Cuộn hoặc chụm để thu phóng · Chạm vào trạm để xem</p>
          {!ready && (
            <div className="dx-running" role="status" data-testid="diagnosis-running">
              <div className="dx-scan" />
              <div className="dx-running-card">
                <Loader2 size={18} className="animate-spin" />
                <div>
                  <strong>Đang chạy chẩn đoán dây chuyền…</strong>
                  <ol>{RUN_STEPS.map((step) => <li key={step}>{step}</li>)}</ol>
                </div>
              </div>
            </div>
          )}
        </div>

        <nav className="dx-flow" aria-label="Luồng sản xuất">
          {STATIONS.map((item, index) => (
            <span key={item.id} className="dx-flow-step">
              {index > 0 && <ChevronRight size={14} className="dx-flow-arrow" aria-hidden="true" />}
              <button
                type="button"
                className={`${item.id === scenario.station ? "fault" : ""} ${item.id === selected ? "selected" : ""}`}
                aria-pressed={item.id === selected}
                onClick={() => select(item.id)}
              >
                <span className="dx-flow-index">{item.index}</span>
                <span>{item.name}</span>
              </button>
            </span>
          ))}
        </nav>

        {/* Covered findings stay out of the tab order until the run ends. */}
        <div className={`dx-content ${ready ? "ready" : ""}`} inert={!ready}>
          {faulty ? (
            <section className="dx-alert" data-testid="diagnosis-suspect">
              <span className="dx-alert-icon"><TriangleAlert size={22} aria-hidden="true" /></span>
              <div>
                <span className="dx-alert-kicker">Nghi vấn (tự động)</span>
                <h3>{fault.name} <small>(No.{fault.index})</small></h3>
                <p>Vấn đề có thể: <strong>{scenario.issue}</strong></p>
                <p className="dx-alert-summary">{scenario.summary}</p>
                <p className="dx-caveat">Giả thuyết từ dữ liệu mô phỏng; cần xác nhận tại máy trước khi xử lý.</p>
              </div>
            </section>
          ) : (
            <section className="dx-ok" data-testid="diagnosis-station-ok">
              <span className="dx-ok-icon"><CircleCheck size={20} aria-hidden="true" /></span>
              <div>
                <h3>{station.name} <small>(No.{station.index})</small></h3>
                <p>Không có dấu hiệu bất thường trong dữ liệu mô phỏng.</p>
                <button type="button" className="dx-link" onClick={() => select(scenario.station)}>
                  Xem trạm nghi vấn: {fault.name} <ChevronRight size={14} />
                </button>
              </div>
            </section>
          )}

          {faulty && (
            <section className="dx-section">
              <header className="dx-section-head"><h3>Triệu chứng phát hiện</h3></header>
              <ul className="dx-metrics">{scenario.metrics.map((metric) => <MetricRow key={metric.id} metric={metric} />)}</ul>
            </section>
          )}

          <Checklist key={scenario.id} scenario={scenario} />
        </div>
      </div>
      <DetailedRun key={scenario.id} />
    </aside>
  );
}

/** Inline chat card for the mock diagnosis "tool call" of one answer. */
export function DiagnosisToolCard({ scenario, running, onOpen }: { scenario: DiagnosisScenario; running: boolean; onOpen(): void }) {
  const station = stationOf(scenario.station);
  return (
    <div className={`dx-tool-card ${running ? "running" : ""}`} data-testid="diagnosis-tool-card">
      <span className="dx-tool-icon">{running ? <Loader2 size={16} className="animate-spin" /> : <Stethoscope size={16} />}</span>
      <span className="dx-tool-text">
        <strong>Chẩn đoán máy</strong>
        <span>{running ? "Đang phân tích dây chuyền…" : `${station.name} (No.${station.index}) · ${scenario.issue}`}</span>
      </span>
      <code>machine_diagnosis</code>
      <button type="button" className="dx-tool-open" onClick={onOpen} disabled={running}>
        <Rotate3d size={15} /> Xem 3D
      </button>
    </div>
  );
}
