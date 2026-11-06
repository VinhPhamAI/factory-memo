/**
 * Mock machine-diagnosis data for the 3D production-line panel.
 *
 * The agent has no diagnosis tool yet. These scenarios are synthetic demo
 * values, not measurements or an approved procedure; the panel labels them so.
 */

export type StationId = "feeder" | "robot" | "processing" | "inspection" | "packing";

export type Station = { id: StationId; index: number; name: string; short: string };

export const STATIONS: Station[] = [
  { id: "feeder", index: 1, name: "Cấp phôi", short: "Material Feeder" },
  { id: "robot", index: 2, name: "Robot gắp đặt", short: "Pick & Place Robot" },
  { id: "processing", index: 3, name: "Máy gia công", short: "Processing Machine" },
  { id: "inspection", index: 4, name: "Kiểm tra", short: "Inspection" },
  { id: "packing", index: 5, name: "Đóng gói / Xuất", short: "Packing / Output" },
];

export type Metric = {
  id: string;
  label: string;
  value: number;
  unit: string;
  /** Upper bound of the normal range, in the same unit as `value`. */
  limit: number;
  /** Text describing the normal range or target. */
  normal: string;
  icon: "temperature" | "vibration" | "cycle" | "rate";
  /** Prefix for values that are changes against a target. */
  sign?: "+" | "−";
};

export type DiagnosisScenario = {
  id: "overheating" | "vibration" | "feeder-jam" | "inspection-reject";
  line: string;
  station: StationId;
  issue: string;
  /** Short English issue label shown beside the 3D callout. */
  issueShort: string;
  summary: string;
  metrics: Metric[];
  checks: string[];
};

export const SCENARIOS: Record<DiagnosisScenario["id"], DiagnosisScenario> = {
  overheating: {
    id: "overheating",
    line: "Main Line A",
    station: "processing",
    issue: "Quá nhiệt",
    issueShort: "Overheating",
    summary: "Nhiệt độ trục chính và độ rung vượt ngưỡng, chu kỳ gia công kéo dài.",
    metrics: [
      { id: "temperature", label: "Nhiệt độ", value: 92, unit: "°C", limit: 80, normal: "Bình thường ≤ 80 °C", icon: "temperature" },
      { id: "vibration", label: "Rung (RMS)", value: 4.8, unit: "mm/s", limit: 2.5, normal: "Bình thường ≤ 2.5 mm/s", icon: "vibration" },
      { id: "cycle", label: "Thời gian chu kỳ", value: 18, unit: "%", sign: "+", limit: 5, normal: "So với mục tiêu 10.0 s", icon: "cycle" },
    ],
    checks: [
      "Kiểm tra hệ thống làm mát (lưu lượng dung dịch / bơm)",
      "Kiểm tra nhiệt độ và tải trục chính",
      "Kiểm tra phoi tích tụ hoặc kẹt trong vùng gia công",
      "Kiểm tra tình trạng bôi trơn",
      "Kiểm tra rung máy (gá lắp / tình trạng dao)",
    ],
  },
  vibration: {
    id: "vibration",
    line: "Main Line A",
    station: "robot",
    issue: "Rung bất thường khớp J2",
    issueShort: "Abnormal vibration",
    summary: "Rung khớp J2 tăng khi tăng tốc, vị trí đặt phôi lệch nhẹ.",
    metrics: [
      { id: "vibration", label: "Rung khớp J2", value: 6.1, unit: "mm/s", limit: 3, normal: "Bình thường ≤ 3.0 mm/s", icon: "vibration" },
      { id: "temperature", label: "Nhiệt độ động cơ J2", value: 68, unit: "°C", limit: 70, normal: "Bình thường ≤ 70 °C", icon: "temperature" },
      { id: "cycle", label: "Thời gian chu kỳ", value: 9, unit: "%", sign: "+", limit: 5, normal: "So với mục tiêu 4.2 s", icon: "cycle" },
    ],
    checks: [
      "Kiểm tra bu lông bệ robot và gá lắp",
      "Kiểm tra độ rơ hộp số khớp J2",
      "Kiểm tra cáp và ống dẫn bị kéo căng khi chuyển động",
      "So sánh tải trọng gắp với thông số cài đặt",
    ],
  },
  "feeder-jam": {
    id: "feeder-jam",
    line: "Main Line A",
    station: "feeder",
    issue: "Kẹt phôi tại máng cấp",
    issueShort: "Material jam",
    summary: "Tốc độ cấp phôi giảm, cảm biến báo thiếu phôi gián đoạn.",
    metrics: [
      { id: "rate", label: "Tốc độ cấp", value: 34, unit: "%", sign: "−", limit: 10, normal: "Thấp hơn mục tiêu 60 phôi/phút", icon: "rate" },
      { id: "vibration", label: "Biên độ rung phễu", value: 0.6, unit: "mm", limit: 0.5, normal: "Cài đặt 0.4–0.5 mm", icon: "vibration" },
      { id: "cycle", label: "Thời gian chờ phôi", value: 22, unit: "%", sign: "+", limit: 5, normal: "So với chu kỳ dây chuyền", icon: "cycle" },
    ],
    checks: [
      "Kiểm tra phôi biến dạng hoặc dị vật trong phễu",
      "Kiểm tra biên độ và tần số rung phễu",
      "Vệ sinh cảm biến phát hiện phôi",
      "Kiểm tra mức phôi trong phễu",
    ],
  },
  "inspection-reject": {
    id: "inspection-reject",
    line: "Main Line A",
    station: "inspection",
    issue: "Tỉ lệ NG tăng",
    issueShort: "High reject rate",
    summary: "Tỉ lệ loại bỏ tăng nhưng mẫu kiểm tra lại phần lớn đạt.",
    metrics: [
      { id: "rate", label: "Tỉ lệ NG", value: 7.4, unit: "%", limit: 2, normal: "Bình thường ≤ 2 %", icon: "rate" },
      { id: "temperature", label: "Nhiệt độ đèn chiếu", value: 61, unit: "°C", limit: 55, normal: "Bình thường ≤ 55 °C", icon: "temperature" },
      { id: "cycle", label: "Thời gian xử lý ảnh", value: 12, unit: "%", sign: "+", limit: 5, normal: "So với mục tiêu 180 ms", icon: "cycle" },
    ],
    checks: [
      "Kiểm tra ống kính và kính bảo vệ camera bị bẩn",
      "Kiểm tra độ sáng và tuổi thọ đèn chiếu",
      "Kiểm tra vị trí phôi khi chụp ảnh",
      "Đối chiếu ảnh NG với mẫu chuẩn trước khi chỉnh ngưỡng",
    ],
  },
};

/** Fold diacritics and case so Vietnamese text matches ASCII keywords. */
function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "d")
    .toLowerCase();
}

const RULES: [DiagnosisScenario["id"], RegExp][] = [
  ["overheating", /\b(qua nhiet|nhiet do|nong|overheat\w*|temperature|lam mat|coolant)\b/],
  ["feeder-jam", /\b(ket phoi|bi ket|jam\w*|cap phoi|feeder|phieu|thieu phoi)\b/],
  ["inspection-reject", /\b(camera|inspection|kiem tra anh|ng|reject\w*|loai bo)\b/],
  ["vibration", /\b(rung|vibrat\w*|robot|lech vi tri)\b/],
];

const DIAGNOSIS = /\b(chan doan|chuan doan|diagnos\w*|su co|loi may|bao loi|alarm|dung may|may bi|may dang|day chuyen|production line|machine)\b/;

/**
 * Pick the mock scenario a chat message asks about.
 *
 * Args:
 *   message: The user's chat text in Vietnamese or English.
 *
 * Returns:
 *   The first scenario whose symptom keywords match; the overheating scenario
 *   for a general diagnosis request; otherwise null.
 */
export function detectDiagnosisScenario(message: string): DiagnosisScenario | null {
  const text = fold(message);
  for (const [id, pattern] of RULES) if (pattern.test(text)) return SCENARIOS[id];
  return DIAGNOSIS.test(text) ? SCENARIOS.overheating : null;
}

export function stationOf(id: StationId): Station {
  return STATIONS.find((station) => station.id === id) ?? STATIONS[0];
}

/** Format a metric the way the panel shows it, e.g. "+18 %" for a change. */
export function formatMetric(metric: Metric): string {
  return `${metric.sign ?? ""}${metric.value} ${metric.unit}`;
}
