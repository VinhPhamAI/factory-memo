import { apiBaseUrl } from "./config";
import type {
  IngestionStatus,
  JobState,
  ReviewStatus,
  VoiceConversation,
  VoiceRecord,
} from "./voice-api";

export type SourceKind = "upload" | "conversation" | "document";
export type ExtractionMode = "knowledge" | "documents";

/** A queued stage for a source; the job workers run convert, then extract. */
export interface SourceJob {
  id: string;
  stage: "convert" | "extract";
  status: "queued" | "running" | "done" | "failed";
  mode: ExtractionMode | null;
  /** Stages queued automatically after this one (Extract on a raw file). */
  next_stages: string[];
  attempts: number;
  max_attempts: number;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** Set while a failed attempt waits for its retry. */
  retry_at: string | null;
}

export type ProcessingState = Omit<IngestionStatus, "status"> & {
  /** "unavailable" when the evidence row could not be read. */
  status?: JobState | "unavailable";
  mode?: ExtractionMode;
  job?: SourceJob;
};

export interface ManagedSource {
  id: string;
  kb: string;
  kind: SourceKind;
  name: string;
  content_type: string;
  created_at: string | null;
  stage: "raw" | "converted" | "extracted";
  mode: ExtractionMode | null;
  review: "none" | "pending" | "reviewed" | "rejected";
  counts: Partial<Record<ReviewStatus, number>>;
  processing: ProcessingState;
  can_convert: boolean;
  can_extract_raw: boolean;
  conversion_note?: string | null;
  source_kind?: string;
}

export interface ManagedChunk {
  id: string;
  doc_id: string;
  index: number;
  text: string;
  title: string;
  status: ReviewStatus;
  history: { decision: string; note?: string | null; at: string }[];
}

export interface ManagedSourceDetail extends ManagedSource {
  text: string | null;
  text_truncated?: boolean;
  records: VoiceRecord[];
  chunks: ManagedChunk[];
  conversation: VoiceConversation | null;
  evidence_id?: string;
}

export interface SourceInventory {
  sources: ManagedSource[];
  warnings: string[];
  next_offset: number | null;
  qa_mode: ExtractionMode;
}

const base = (kb: string) =>
  `${apiBaseUrl}/v1/knowledge-management/${encodeURIComponent(kb)}`;
const sourcePath = (kb: string, kind: SourceKind, id: string) =>
  `${base(kb)}/sources/${kind}/${encodeURIComponent(id)}`;
export const sourceLink = (source: Pick<ManagedSource, "kb" | "kind" | "id">) =>
  `/knowledge/${encodeURIComponent(source.kb)}/sources/${source.kind}/${encodeURIComponent(source.id)}`;
export const originalDocumentUrl = (source: ManagedSource) =>
  `${sourcePath(source.kb, "upload", source.id)}/original`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Không kết nối được máy chủ. Hãy thử lại.", {
      cause: error,
    });
  }
  if (!response.ok) {
    if (response.status === 404)
      throw new Error(
        "Không tìm thấy nguồn dữ liệu hoặc API chưa được cập nhật.",
      );
    if (response.status === 409)
      throw new Error(
        "Nguồn đang được xử lý hoặc đã có kết quả. Hãy làm mới để xem trạng thái.",
      );
    if (response.status === 422)
      throw new Error(
        "Không xử lý được dữ liệu. File text cần dùng UTF-8/UTF-16 và có nội dung; trích xuất knowledge items giới hạn 100.000 ký tự.",
      );
    throw new Error(
      `Không xử lý được yêu cầu (HTTP ${response.status}). Kiểm tra dịch vụ rồi thử lại.`,
    );
  }
  return response.json() as Promise<T>;
}

export const listManagedSources = (
  kb: string,
  signal?: AbortSignal,
  offset = 0,
) =>
  request<SourceInventory>(`${base(kb)}/sources?offset=${offset}`, { signal });
export const getManagedSource = (
  kb: string,
  kind: SourceKind,
  id: string,
  signal?: AbortSignal,
) => request<ManagedSourceDetail>(sourcePath(kb, kind, id), { signal });
export const convertManagedSource = (kb: string, id: string) =>
  request<{ job: SourceJob }>(`${sourcePath(kb, "upload", id)}/convert`, {
    method: "POST",
  });
export const extractManagedSource = (
  kb: string,
  kind: SourceKind,
  id: string,
  mode: ExtractionMode,
) =>
  request<{ status: "queued"; mode: ExtractionMode; job: SourceJob }>(
    `${sourcePath(kb, kind, id)}/extract`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode }),
    },
  );

/** The source as it looks right after a job was queued, until the next refresh. */
export const withJob = <T extends ManagedSource>(
  source: T,
  job: SourceJob,
): T => ({
  ...source,
  mode: job.mode ?? source.mode,
  processing: {
    ...source.processing,
    status: job.status,
    mode: job.mode ?? source.processing.mode,
    error: undefined,
    job,
  },
});
export const reviewManagedChunk = (
  source: ManagedSource,
  chunkId: string,
  decision: "approve" | "reject" | "reset",
  note: string,
) =>
  request<ManagedSourceDetail>(
    `${sourcePath(source.kb, source.kind, source.id)}/review-chunk`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chunk_id: chunkId, decision, note }),
    },
  );
