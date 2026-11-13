/**
 * Declarative description of machines for the 3D diagnosis viewer.
 *
 * A machine is a list of placed components. Each component names a builder
 * from the 3D component library (`kind`) and its parameters, so new machines
 * reuse the library instead of modelling geometry again. Nothing here imports
 * three.js; the panel can read machines and scenarios before the 3D chunk loads.
 */

export type Vec3 = readonly [number, number, number];

/** Component builders in `components/machine-3d/library`. */
export type ComponentKind =
  | "base-frame"
  | "enclosure"
  | "cabinet"
  | "safety-fence"
  | "worktable"
  | "linear-axis"
  | "spindle"
  | "tool-changer"
  | "motor"
  | "gearbox"
  | "conveyor"
  | "roller-conveyor"
  | "robot-arm"
  | "gripper"
  | "pneumatic-cylinder"
  | "valve-manifold"
  | "plc"
  | "control-panel"
  | "signal-tower"
  | "sensor"
  | "light-curtain"
  | "coolant-unit"
  | "workpiece-flow"
  | "fixture";

/** UI groups for the component list; order follows this list. */
export type ComponentGroup = "mechanism" | "drive" | "sensing" | "control" | "safety" | "structure";

export const COMPONENT_GROUPS: { id: ComponentGroup; label: string }[] = [
  { id: "mechanism", label: "Cơ cấu chính" },
  { id: "drive", label: "Truyền động" },
  { id: "sensing", label: "Cảm biến" },
  { id: "control", label: "Điều khiển" },
  { id: "safety", label: "An toàn" },
  { id: "structure", label: "Khung & vỏ" },
];

/** Vietnamese names of component kinds, shown beside a selected component. */
export const KIND_LABELS: Record<ComponentKind, string> = {
  "base-frame": "Khung đế",
  enclosure: "Vỏ bao che",
  cabinet: "Tủ điện",
  "safety-fence": "Hàng rào an toàn",
  worktable: "Bàn máy",
  "linear-axis": "Trục tuyến tính",
  spindle: "Trục chính",
  "tool-changer": "Bộ thay dao",
  motor: "Động cơ",
  gearbox: "Hộp số",
  conveyor: "Băng tải",
  "roller-conveyor": "Băng tải con lăn",
  "robot-arm": "Tay robot",
  gripper: "Tay gắp",
  "pneumatic-cylinder": "Xi lanh khí nén",
  "valve-manifold": "Cụm van điện từ",
  plc: "PLC",
  "control-panel": "Bảng điều khiển",
  "signal-tower": "Đèn tháp",
  sensor: "Cảm biến",
  "light-curtain": "Màn chắn sáng",
  "coolant-unit": "Bể & bơm làm mát",
  "workpiece-flow": "Phôi",
  fixture: "Đồ gá",
};

export type ParamValue = number | string | boolean | readonly number[];
export type ComponentParams = Readonly<Record<string, ParamValue>>;

export type ComponentSpec = {
  /** Unique within the machine; used by scenarios, checklists and the URL-free selection state. */
  id: string;
  kind: ComponentKind;
  label: string;
  /** Short technical note shown with the selection, e.g. "Servo 1.5 kW". */
  spec?: string;
  group: ComponentGroup;
  /** Position in the parent space: the machine, or the socket it is mounted on. */
  position?: Vec3;
  /** Euler XYZ rotation in radians. */
  rotation?: Vec3;
  /** Mount on another component's named socket instead of the machine floor. */
  mount?: { component: string; socket: string };
  params?: ComponentParams;
  /** Outer guard or housing; drawn see-through in x-ray view. */
  shell?: boolean;
  /** Sits inside a shell; selecting it turns x-ray view on. */
  interior?: boolean;
  /** Decorative parts (floor plates, product flow) are not listed or picked. */
  selectable?: boolean;
};

export type MachineType = "cnc" | "conveyor" | "robot-cell";

export type MachineDefinition = {
  id: string;
  type: MachineType;
  name: string;
  /** Generic model description; not a vendor product. */
  model: string;
  summary: string;
  /** Default camera position and orbit target in metres. */
  camera: { position: Vec3; target: Vec3 };
  components: ComponentSpec[];
  /** Folded (diacritic-free, lower-case) words that name this machine in chat or tickets. */
  keywords: RegExp;
};

export type ComponentStatus = "normal" | "fault" | "related";

export type Metric = {
  id: string;
  label: string;
  value: number;
  unit: string;
  /** Upper bound of the normal range, in the same unit as `value`. */
  limit: number;
  /** Text describing the normal range or target. */
  normal: string;
  icon: "temperature" | "vibration" | "cycle" | "rate" | "pressure" | "current";
  /** Prefix for values that are changes against a target. */
  sign?: "+" | "−";
};

export type CheckItem = {
  text: string;
  /** Component the check is done on; the checklist can focus it in 3D. */
  component?: string;
};

/**
 * One synthetic fault case on one machine. Values are demo data, not
 * measurements or an approved procedure; the panel labels them so.
 */
export type FaultScenario = {
  id: string;
  machine: string;
  /** Suspected component, highlighted in red. */
  component: string;
  /** Components that may contribute, highlighted in amber. */
  related: string[];
  issue: string;
  /** Short English label shown on the 3D marker and HMI screen. */
  issueShort: string;
  summary: string;
  metrics: Metric[];
  checks: CheckItem[];
  /** How the 3D view shows the suspected component; motion effects stay subtle. */
  effect?: "heat" | "vibration" | "stall";
  /** Folded words that point to this fault in chat or ticket text. */
  keywords: RegExp;
};

/** What the viewer shows: a machine and optionally one fault on it. */
export type MachineView = { machine: string; scenario?: string };
