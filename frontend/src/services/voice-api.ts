import type { KnowledgeBase } from "@/types";
import { apiBaseUrl } from "./config";

export type JobState =
  "off" | "queued" | "running" | "done" | "failed" | "interrupted";

export interface VoiceTurn {
  id: string;
  /** Diarizer letter (A, B, …); null when the recognizer could not tell. */
  speaker: string | null;
  text: string;
  start: number | null;
  end: number | null;
}

export interface CleanedTurn {
  text: string;
  /** Set when the cleaned text dropped a number, so the raw text was kept. */
  kept_raw?: string;
  missing_numbers?: string[];
}

export interface CleaningStatus {
  status?: JobState;
  enabled?: boolean;
  total?: number;
  turns?: Record<string, CleanedTurn>;
  error?: string;
  model?: string;
}

export interface MergedLesson {
  subject: string;
  claim: string;
  type: string;
  target: string | null;
}

export interface IngestionStatus {
  status?: JobState;
  /** extracting | verifying | saving while running. */
  stage?: string;
  started_at?: string;
  finished_at?: string;
  error?: string;
  stored?: number;
  merged?: MergedLesson[];
}

export interface VerifyResult {
  passed?: boolean;
  issues?: string[];
  method?: string;
  verdict?: string;
  error?: string;
}

export type ReviewStatus = "draft" | "approved" | "deprecated";

export interface VoiceRecord {
  id: string;
  subject: string;
  claim: string;
  type: string;
  status: ReviewStatus;
  context: Record<string, unknown>;
  stated_by: string | null;
  created_at: string;
  superseded_by: string | null;
  verify: VerifyResult | null;
  consolidation: { action?: string; reason?: string; target?: string } | null;
  history: { decision?: string; note?: string | null; at?: string }[];
}

export interface Speaker {
  label: string;
  name: string | null;
}

export interface VoiceConversation {
  id: string;
  kb: string;
  name: string;
  recorded_at: string | null;
  created_at: string;
  status: "pending" | "processed" | "failed";
  audio: { available: boolean; file: string | null; duration_s: number | null };
  asr: { recognition?: "realtime" | "file"; language?: string };
  speakers: Speaker[];
  turns: VoiceTurn[];
  cleaning: CleaningStatus;
  ingestion: IngestionStatus;
  records: VoiceRecord[];
  merged_into: VoiceRecord[];
}

export interface VoiceConversationSummary {
  id: string;
  name: string;
  recorded_at: string | null;
  duration_s: number | null;
  speakers: number;
  ingestion: { status?: JobState; stage?: string | null };
  knowledge: Partial<Record<ReviewStatus, number>>;
}

const base = () => `${apiBaseUrl}/v1/voice`;
const kbPath = (kb: string) => `${base()}/${encodeURIComponent(kb)}`;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.", {
      cause: error,
    });
  }
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Not JSON; the status code is enough.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(
      status === 404
        ? "Không tìm thấy dữ liệu này."
        : status === 503
          ? "Cơ sở dữ liệu tri thức chưa sẵn sàng."
          : detail || `Máy chủ trả lỗi HTTP ${status}.`,
    );
  }
}

export async function listCaptureKnowledgeBases(
  signal?: AbortSignal,
): Promise<KnowledgeBase[]> {
  const body = await request<{ knowledge_bases: KnowledgeBase[] }>(
    `${base()}/knowledge-bases`,
    { signal },
  );
  return body.knowledge_bases;
}

export async function listConversations(
  kb: string,
  signal?: AbortSignal,
): Promise<VoiceConversationSummary[]> {
  const body = await request<{ conversations: VoiceConversationSummary[] }>(
    `${kbPath(kb)}/conversations`,
    { signal },
  );
  return body.conversations;
}

export function getConversation(kb: string, id: string, signal?: AbortSignal) {
  return request<VoiceConversation>(`${kbPath(kb)}/conversations/${id}`, {
    signal,
  });
}

export function renameSpeakers(
  kb: string,
  id: string,
  speakers: Record<string, string | null>,
) {
  return request<VoiceConversation>(
    `${kbPath(kb)}/conversations/${id}/speakers`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ speakers }),
    },
  );
}

export function reviewRecord(
  kb: string,
  recordId: string,
  decision: "approve" | "reject" | "reset",
  note = "",
) {
  return request<VoiceRecord>(`${kbPath(kb)}/knowledge/${recordId}/review`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ decision, note }),
  });
}

export function retryExtraction(kb: string, id: string) {
  return request<{ status: string }>(
    `${kbPath(kb)}/conversations/${id}/extract`,
    { method: "POST" },
  );
}

export function startCleaning(kb: string, id: string) {
  return request<{ status: string }>(
    `${kbPath(kb)}/conversations/${id}/clean`,
    { method: "POST" },
  );
}

export function audioUrl(kb: string, id: string) {
  return `${kbPath(kb)}/conversations/${id}/audio`;
}

/** Whole recording kept by the browser, sent when the live socket was lost. */
export async function uploadRecording(
  kb: string,
  form: { name: string; clean: boolean; recordedAt: Date; audio: Blob },
): Promise<string> {
  const data = new FormData();
  data.set("name", form.name);
  data.set("clean", String(form.clean));
  data.set("recorded_at", form.recordedAt.toISOString());
  data.set("audio", form.audio, "recording.wav");
  const body = await request<{ conversation_id: string }>(
    `${kbPath(kb)}/recordings`,
    { method: "POST", body: data },
  );
  return body.conversation_id;
}

export function recordSocketUrl(kb: string, origin = window.location) {
  const target = apiBaseUrl
    ? new URL(apiBaseUrl, origin.href)
    : new URL(origin.href);
  const scheme = target.protocol === "https:" ? "wss:" : "ws:";
  const prefix = apiBaseUrl ? target.pathname.replace(/\/+$/, "") : "";
  return `${scheme}//${target.host}${prefix}/v1/voice/${encodeURIComponent(kb)}/record`;
}
