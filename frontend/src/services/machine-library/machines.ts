import type { MachineDefinition } from "./types";

/**
 * Built-in machines assembled from the component library. Dimensions are in
 * metres and approximate common equipment; they are generic models, not a
 * particular vendor's product or a measured factory layout.
 */

const HALF = Math.PI / 2;

export const CNC_MACHINE: MachineDefinition = {
  id: "cnc-vmc",
  type: "cnc",
  name: "Trung tâm gia công CNC",
  model: "Máy phay đứng 3 trục · ổ dao 20 vị trí",
  summary: "Thân gang, trục X/Y/Z dùng vít me bi và servo, trục chính đứng, bơm dung dịch làm mát riêng.",
  camera: { position: [3.9, 2.9, 4.5], target: [0, 1.2, 0.1] },
  keywords: /\b(cnc|vmc|may phay|may gia cong|trung tam gia cong|machining|milling)\b/,
  components: [
    { id: "base", kind: "base-frame", label: "Thân máy & trụ đứng", spec: "Gang đúc", group: "structure", params: { style: "vmc" }, interior: true },
    { id: "enclosure", kind: "enclosure", label: "Vỏ bao che & cửa", group: "structure", shell: true, position: [0, 0, 0.05], params: { width: 2.5, depth: 2.2, height: 2.55 } },
    { id: "y-axis", kind: "linear-axis", label: "Trục Y", spec: "Vít me bi · hành trình 500 mm", group: "drive", interior: true, mount: { component: "base", socket: "y-axis" }, rotation: [0, -HALF, 0], params: { length: 1.0, width: 0.8, carriageLength: 0.5, carriage: 0.5 } },
    { id: "x-axis", kind: "linear-axis", label: "Trục X", spec: "Vít me bi · hành trình 850 mm", group: "drive", interior: true, mount: { component: "y-axis", socket: "carriage" }, rotation: [0, HALF, 0], params: { length: 1.3, width: 0.5, carriageLength: 0.7, carriage: 0.45 } },
    { id: "table", kind: "worktable", label: "Bàn máy & ê tô", spec: "Bàn 1000 × 460 mm", group: "mechanism", interior: true, mount: { component: "x-axis", socket: "carriage" }, params: { width: 1.0, depth: 0.46 } },
    { id: "z-axis", kind: "linear-axis", label: "Trục Z", spec: "Vít me bi · hành trình 500 mm", group: "drive", interior: true, mount: { component: "base", socket: "z-axis" }, rotation: [0, HALF, HALF], params: { length: 0.8, width: 0.42, carriageLength: 0.3, carriage: 0.72 } },
    { id: "spindle", kind: "spindle", label: "Trục chính", spec: "BT40 · 12 000 v/ph", group: "mechanism", interior: true, mount: { component: "z-axis", socket: "carriage" }, rotation: [-HALF, 0, -HALF], params: { reach: 0.36 } },
    { id: "spindle-motor", kind: "motor", label: "Động cơ trục chính", spec: "11 kW", group: "drive", interior: true, mount: { component: "spindle", socket: "motor" }, params: { type: "servo", radius: 0.1, length: 0.28, finish: "graphite" } },
    { id: "spindle-temp", kind: "sensor", label: "Cảm biến nhiệt trục chính", spec: "Thermocouple loại K", group: "sensing", interior: true, mount: { component: "spindle", socket: "sensor" }, params: { variant: "temperature" } },
    { id: "servo-x", kind: "motor", label: "Servo trục X", spec: "Servo AC 3 kW", group: "drive", interior: true, mount: { component: "x-axis", socket: "motor" }, params: { type: "servo", radius: 0.065, length: 0.22 } },
    { id: "servo-y", kind: "motor", label: "Servo trục Y", spec: "Servo AC 3 kW", group: "drive", interior: true, mount: { component: "y-axis", socket: "motor" }, params: { type: "servo", radius: 0.065, length: 0.22 } },
    { id: "servo-z", kind: "motor", label: "Servo trục Z", spec: "Servo AC 3 kW, có phanh", group: "drive", interior: true, mount: { component: "z-axis", socket: "motor" }, params: { type: "servo", radius: 0.07, length: 0.24 } },
    { id: "atc", kind: "tool-changer", label: "Ổ thay dao (ATC)", spec: "20 vị trí", group: "mechanism", interior: true, mount: { component: "base", socket: "atc" }, params: { pockets: 20, radius: 0.26 } },
    { id: "panel", kind: "control-panel", label: "Bảng điều khiển CNC", group: "control", mount: { component: "enclosure", socket: "panel" }, params: { style: "cnc" } },
    { id: "door-switch", kind: "sensor", label: "Công tắc khóa cửa", spec: "Khóa liên động an toàn", group: "safety", mount: { component: "enclosure", socket: "door-switch" }, params: { variant: "interlock" } },
    { id: "tower", kind: "signal-tower", label: "Đèn tháp", group: "control", mount: { component: "enclosure", socket: "roof" }, params: { pole: 0.18 } },
    { id: "coolant", kind: "coolant-unit", label: "Bể & bơm dung dịch làm mát", spec: "Bơm 0.4 kW · bể 200 L", group: "mechanism", position: [-1.62, 0, -0.2], params: { pipe: 0.5 } },
    { id: "cabinet", kind: "cabinet", label: "Tủ điện CNC", group: "control", position: [0.75, 0, -1.5], rotation: [0, Math.PI, 0], params: { width: 0.8, height: 1.8, depth: 0.42 } },
  ],
};

export const CONVEYOR_SYSTEM: MachineDefinition = {
  id: "conveyor-line",
  type: "conveyor",
  name: "Hệ thống băng tải phân loại",
  model: "Băng tải đai 5.6 m · xi lanh đẩy loại · camera kiểm tra",
  summary: "Băng tải đai dẫn động bằng động cơ giảm tốc, camera kiểm tra, cảm biến quang và xi lanh đẩy sản phẩm NG sang làn loại.",
  camera: { position: [3.6, 3.1, 5.2], target: [0.2, 0.7, 0] },
  keywords: /\b(bang tai|bang chuyen|conveyor|belt|phan loai|sorting)\b/,
  components: [
    { id: "main-belt", kind: "conveyor", label: "Băng tải chính", spec: "Đai PVC 500 mm · 0.3 m/s", group: "mechanism", params: { length: 5.6, width: 0.5, height: 0.85, speed: 0.3, legSpacing: 1.35 } },
    { id: "products", kind: "workpiece-flow", label: "Sản phẩm", group: "mechanism", selectable: false, mount: { component: "main-belt", socket: "top" }, params: { length: 5.4, count: 9, size: 0.22, speed: 0.3, finish: "cardboard" } },
    { id: "gearbox", kind: "gearbox", label: "Hộp giảm tốc", spec: "Bánh răng côn-xoắn · i = 25", group: "drive", mount: { component: "main-belt", socket: "drive" }, params: { size: 0.17 } },
    { id: "drive-motor", kind: "motor", label: "Động cơ băng tải", spec: "Động cơ 3 pha 0.75 kW", group: "drive", mount: { component: "gearbox", socket: "input" }, params: { type: "induction", radius: 0.075, length: 0.28, finish: "blue" } },
    { id: "pe-entry", kind: "sensor", label: "Cảm biến quang đầu vào", spec: "Phản xạ gương", group: "sensing", mount: { component: "main-belt", socket: "side-left" }, position: [-2.2, 0, 0], params: { variant: "photoelectric", beam: 0.62 } },
    { id: "vision", kind: "sensor", label: "Camera kiểm tra", spec: "Camera 5 MP · đèn vòng", group: "sensing", position: [-0.8, 0, -0.42], params: { variant: "camera", mast: 1.45, reach: 0.42 } },
    { id: "pe-divert", kind: "sensor", label: "Cảm biến quang điểm loại", spec: "Phản xạ gương", group: "sensing", mount: { component: "main-belt", socket: "side-left" }, position: [1.05, 0, 0], params: { variant: "photoelectric", beam: 0.62 } },
    { id: "pusher-stand", kind: "base-frame", label: "Giá xi lanh", group: "structure", position: [1.5, 0, -0.85], params: { style: "post", height: 0.86 } },
    { id: "pusher", kind: "pneumatic-cylinder", label: "Xi lanh đẩy loại", spec: "Ø50 × 620 mm", group: "mechanism", position: [1.5, 0.92, -1.13], rotation: [HALF, 0, 0], params: { bore: 0.025, stroke: 0.62, tool: "plate", cycle: 6, offset: 1 } },
    { id: "pusher-reed", kind: "sensor", label: "Công tắc từ cuối hành trình", spec: "Reed switch", group: "sensing", mount: { component: "pusher", socket: "reed-extended" }, params: { variant: "reed" } },
    { id: "reject-lane", kind: "roller-conveyor", label: "Làn sản phẩm loại (NG)", group: "mechanism", position: [1.5, 0, 0.98], rotation: [0, -HALF, 0], params: { length: 1.2, width: 0.5, height: 0.8 } },
    { id: "cabinet", kind: "cabinet", label: "Tủ điện băng tải", group: "control", shell: true, position: [2.1, 0, -1.45], params: { width: 0.7, height: 1.6, depth: 0.4, door: "glass" } },
    { id: "plc", kind: "plc", label: "PLC điều khiển", spec: "CPU + 6 mô-đun I/O", group: "control", interior: true, mount: { component: "cabinet", socket: "din-rail" }, params: { modules: 6 } },
    { id: "valves", kind: "valve-manifold", label: "Cụm van điện từ", spec: "4 van 5/2", group: "control", mount: { component: "cabinet", socket: "side-left" }, params: { stations: 4 } },
    { id: "tower", kind: "signal-tower", label: "Đèn tháp", group: "control", mount: { component: "cabinet", socket: "top" } },
    { id: "station", kind: "control-panel", label: "Hộp nút vận hành", spec: "Start / Stop / Dừng khẩn", group: "safety", position: [-2.55, 0, 0.75], params: { style: "station" } },
  ],
};

export const ROBOT_CELL: MachineDefinition = {
  id: "robot-cell",
  type: "robot-cell",
  name: "Trạm robot gắp đặt",
  model: "Robot 6 trục · tầm với 1.2 m · hàng rào an toàn",
  summary: "Robot gắp phôi từ băng tải cấp, đặt vào đồ gá có xi lanh kẹp; cell có màn chắn sáng và công tắc cửa.",
  camera: { position: [3.7, 4.4, 4.3], target: [0, 0.6, 0] },
  keywords: /\b(robot|tay may|tram robot|robot cell|gap dat|pick and place|pick & place)\b/,
  components: [
    { id: "floor", kind: "base-frame", label: "Sàn cell", group: "structure", selectable: false, params: { style: "plate", width: 4.4, depth: 4.0 } },
    { id: "fence", kind: "safety-fence", label: "Hàng rào an toàn", spec: "Cao 1.9 m", group: "safety", shell: true, params: { width: 4.0, depth: 3.6, height: 1.9, panel: 1, door: 3, opening: 2 } },
    { id: "pedestal", kind: "base-frame", label: "Bệ robot", interior: true, group: "structure", position: [0, 0, -0.2], params: { style: "pedestal", height: 0.45 } },
    { id: "robot", kind: "robot-arm", label: "Tay robot 6 trục", interior: true, spec: "Tải 10 kg · tầm với 1.2 m", group: "mechanism", mount: { component: "pedestal", socket: "top" }, params: { pick: [-0.85, 0.48, 0.65], place: [0.95, 0.5, 0.55], tool: 0.16, cycle: 7 } },
    { id: "j1-motor", kind: "motor", label: "Servo khớp J1", interior: true, spec: "Servo + hộp giảm tốc", group: "drive", mount: { component: "robot", socket: "j1-motor" }, params: { type: "servo", radius: 0.055, length: 0.16, finish: "paintDark" } },
    { id: "j2-motor", kind: "motor", label: "Servo khớp J2", interior: true, spec: "Servo + hộp giảm tốc", group: "drive", mount: { component: "robot", socket: "j2-motor" }, params: { type: "servo", radius: 0.06, length: 0.16, finish: "paintDark" } },
    { id: "j3-motor", kind: "motor", label: "Servo khớp J3", interior: true, spec: "Servo + hộp giảm tốc", group: "drive", mount: { component: "robot", socket: "j3-motor" }, params: { type: "servo", radius: 0.05, length: 0.14, finish: "paintDark" } },
    { id: "gripper", kind: "gripper", label: "Tay gắp khí nén", interior: true, spec: "Kẹp song song · hành trình 40 mm", group: "mechanism", mount: { component: "robot", socket: "flange" }, params: { cycle: 7, closeAt: 0.17, openAt: 0.72 } },
    { id: "gripper-sensor", kind: "sensor", label: "Cảm biến có phôi", interior: true, spec: "Tiệm cận M12", group: "sensing", mount: { component: "gripper", socket: "sensor" }, params: { variant: "proximity" } },
    { id: "infeed", kind: "conveyor", label: "Băng tải cấp phôi", spec: "Đai 400 mm", group: "mechanism", position: [-1.85, 0, 0.45], params: { length: 2.0, width: 0.4, height: 0.85, speed: 0.2, legSpacing: 1.0 } },
    { id: "parts", kind: "workpiece-flow", label: "Phôi", group: "mechanism", selectable: false, mount: { component: "infeed", socket: "top" }, params: { length: 1.9, count: 5, size: 0.12, speed: 0.2, finish: "workpiece" } },
    { id: "pe-pick", kind: "sensor", label: "Cảm biến vị trí gắp", interior: true, spec: "Phản xạ gương", group: "sensing", mount: { component: "infeed", socket: "side-left" }, position: [0.8, 0, 0], params: { variant: "photoelectric", beam: 0.52 } },
    { id: "fixture", kind: "fixture", label: "Đồ gá lắp ráp", interior: true, group: "mechanism", position: [0.95, 0, 0.35], params: { width: 0.9, depth: 0.7, height: 0.82 } },
    { id: "clamp-a", kind: "pneumatic-cylinder", label: "Xi lanh kẹp A", interior: true, spec: "Ø32 × 50 mm", group: "mechanism", mount: { component: "fixture", socket: "clamp-a" }, params: { bore: 0.016, stroke: 0.05, tool: "clamp" } },
    { id: "clamp-b", kind: "pneumatic-cylinder", label: "Xi lanh kẹp B", interior: true, spec: "Ø32 × 50 mm", group: "mechanism", mount: { component: "fixture", socket: "clamp-b" }, params: { bore: 0.016, stroke: 0.05, tool: "clamp" } },
    { id: "valves", kind: "valve-manifold", label: "Cụm van điện từ", interior: true, spec: "3 van 5/2", group: "control", mount: { component: "fixture", socket: "side" }, params: { stations: 3 } },
    { id: "light-curtain", kind: "light-curtain", label: "Màn chắn sáng", spec: "Loại 4 · độ phân giải 30 mm", group: "safety", mount: { component: "fence", socket: "opening" }, params: { width: 0.82, height: 1.35, base: 0.2 } },
    { id: "door-switch", kind: "sensor", label: "Công tắc cửa an toàn", spec: "Khóa liên động", group: "safety", mount: { component: "fence", socket: "door" }, params: { variant: "interlock" } },
    { id: "tower", kind: "signal-tower", label: "Đèn tháp", group: "control", mount: { component: "fence", socket: "corner" } },
    { id: "controller", kind: "cabinet", label: "Tủ điều khiển robot", group: "control", shell: true, position: [1.2, 0, -2.35], params: { width: 0.65, height: 1.4, depth: 0.45, door: "glass" } },
    { id: "robot-plc", kind: "plc", label: "PLC cell", spec: "CPU + 4 mô-đun I/O an toàn", group: "control", interior: true, mount: { component: "controller", socket: "din-rail" }, params: { modules: 4 } },
    { id: "station", kind: "control-panel", label: "Hộp nút vận hành", spec: "Start / Stop / Dừng khẩn", group: "safety", position: [0.5, 0, 2.15], params: { style: "station" } },
  ],
};

export const BUILT_IN_MACHINES: MachineDefinition[] = [CNC_MACHINE, CONVEYOR_SYSTEM, ROBOT_CELL];
