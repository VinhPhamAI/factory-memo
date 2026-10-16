import type {
  IngestionStatus,
  Speaker,
  VoiceRecord,
  VoiceTurn,
} from "./voice-api";

export function clock(totalSeconds: number | null | undefined) {
  const seconds = Math.max(0, Math.floor(totalSeconds ?? 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function dateTime(iso: string | null | undefined) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function defaultConversationName(now = new Date()) {
  const date = now.toLocaleDateString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
  });
  const time = now.toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });
  return `Hội thoại ${date} ${time}`;
}

/** Stable colour slot per speaker letter: A→0, B→1, … wrapping after 6. */
export function speakerSlot(label: string | null) {
  if (!label) return "unknown";
  const index = [...label].reduce(
    (total, char) => total * 26 + char.charCodeAt(0) - 64,
    0,
  );
  return String((index - 1) % 6);
}

export function speakerName(label: string | null, speakers: Speaker[]) {
  if (!label) return "Không rõ người nói";
  const name = speakers.find((speaker) => speaker.label === label)?.name;
  return name ? `${name}` : `Người nói ${label}`;
}

export const TYPE_LABELS: Record<string, string> = {
  case: "Tình huống",
  heuristic: "Kinh nghiệm",
  procedure: "Quy trình",
  rule: "Quy tắc",
  fact: "Thông tin",
};

export const CERTAINTY_LABELS: Record<string, string> = {
  stated: "Được khẳng định",
  uncertain: "Chưa chắc chắn",
  planned: "Dự định, chưa làm",
};

export const STATUS_LABELS: Record<string, string> = {
  draft: "Chờ duyệt",
  approved: "Đã duyệt",
  deprecated: "Đã loại",
};

export interface Evidence {
  turnId: string | null;
  quote: string;
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** Verbatim quotes a record cites: general items' quotes or a Case Card's evidence. */
export function recordEvidence(record: VoiceRecord): Evidence[] {
  const sources = [
    record.context.quotes,
    isObject(record.context.case_card)
      ? record.context.case_card.evidence
      : undefined,
  ];
  const evidence: Evidence[] = [];
  for (const source of sources) {
    if (!Array.isArray(source)) continue;
    for (const item of source) {
      if (!isObject(item) || typeof item.quote !== "string" || !item.quote)
        continue;
      const turn = item.turn_id;
      evidence.push({
        quote: item.quote,
        turnId: typeof turn === "string" && turn ? turn : null,
      });
    }
  }
  return evidence;
}

export function recordSteps(record: VoiceRecord): string[] {
  const steps = record.context.steps;
  return Array.isArray(steps)
    ? steps.filter((step): step is string => typeof step === "string")
    : [];
}

/** The claim without the numbered steps the extractor appends to procedures. */
export function claimText(record: VoiceRecord) {
  const steps = recordSteps(record);
  if (!steps.length) return record.claim;
  return record.claim.split("\n1. ")[0];
}

const HIDDEN_CONTEXT = new Set([
  "quotes",
  "steps",
  "case_card",
  "source_turns",
  "certainty",
  "speaker",
  "benchmark_run_id",
]);

const CONTEXT_LABELS: Record<string, string> = {
  applies_to: "Phạm vi áp dụng",
  condition: "Điều kiện",
  equipment: "Thiết bị",
  asset: "Thiết bị",
  machine: "Máy",
  component: "Bộ phận",
  line: "Line",
};

/** Scope fields worth showing a reviewer, as label/value pairs. */
export function recordScope(record: VoiceRecord): [string, string][] {
  return Object.entries(record.context)
    .filter(([key, value]) => !HIDDEN_CONTEXT.has(key) && value != null)
    .map(([key, value]): [string, string] => [
      CONTEXT_LABELS[key] ?? key.replaceAll("_", " "),
      typeof value === "string" ? value : JSON.stringify(value),
    ])
    .filter(([, value]) => value !== "" && value !== "[]" && value !== "{}");
}

export function recordSpeaker(record: VoiceRecord): string | null {
  const speaker = record.context.speaker;
  if (typeof speaker !== "string") return null;
  const match = /^Speaker ([A-Z]+)$/.exec(speaker.trim());
  return match ? match[1] : null;
}

export type StepState = "done" | "active" | "waiting" | "failed";

export interface PipelineStep {
  key: string;
  label: string;
  state: StepState;
}

/** Saved → extracting → checking quotes → saving to the KB → ready to review. */
export function pipelineSteps(ingestion: IngestionStatus): PipelineStep[] {
  const order = ["extracting", "verifying", "saving"];
  const status = ingestion.status ?? "queued";
  const current = status === "running" ? (ingestion.stage ?? "extracting") : "";
  const position = order.indexOf(current);
  const steps: PipelineStep[] = [
    { key: "saved", label: "Đã lưu bản ghi", state: "done" },
    { key: "extracting", label: "Trích xuất tri thức", state: "waiting" },
    { key: "verifying", label: "Đối chiếu trích dẫn", state: "waiting" },
    { key: "saving", label: "Lưu vào knowledge base", state: "waiting" },
  ];
  for (const [index, step] of steps.slice(1).entries()) {
    if (status === "done") step.state = "done";
    else if (status === "running")
      step.state =
        index < position ? "done" : index === position ? "active" : "waiting";
  }
  if (status === "failed" || status === "interrupted") {
    const failedAt = steps.find((step) => step.key === "extracting")!;
    failedAt.state = "failed";
  }
  return steps;
}

export function stageText(ingestion: IngestionStatus) {
  switch (ingestion.status) {
    case "queued":
      return "Đang chờ bắt đầu trích xuất…";
    case "running":
      return ingestion.stage === "verifying"
        ? "Đang đối chiếu trích dẫn với bản ghi…"
        : ingestion.stage === "saving"
          ? "Đang lưu tri thức vào knowledge base…"
          : "AI đang đọc hội thoại và trích xuất tri thức…";
    case "done":
      return "Đã trích xuất xong";
    case "failed":
      return "Trích xuất thất bại";
    case "interrupted":
      return "Trích xuất bị gián đoạn do máy chủ khởi động lại";
    default:
      return "";
  }
}

/** Quote positions inside a turn, for highlighting; case-insensitive, whitespace-tolerant. */
export function findQuote(
  text: string,
  quote: string,
): [number, number] | null {
  const needle = quote.trim();
  if (!needle) return null;
  const exact = text.toLowerCase().indexOf(needle.toLowerCase());
  if (exact >= 0) return [exact, exact + needle.length];
  const pattern = needle
    .split(/\s+/)
    .map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("\\s+");
  const match = new RegExp(pattern, "i").exec(text);
  return match ? [match.index, match.index + match[0].length] : null;
}

export function turnsBySpeaker(turns: VoiceTurn[]) {
  const counts = new Map<string, number>();
  for (const turn of turns)
    if (turn.speaker)
      counts.set(turn.speaker, (counts.get(turn.speaker) ?? 0) + 1);
  return counts;
}
