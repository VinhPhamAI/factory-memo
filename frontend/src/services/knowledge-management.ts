import { apiBaseUrl } from "./config";
import { sessionClient } from "./auth-api";
import type {
  IngestionStatus,
  JobState,
  ReviewStatus,
  VoiceConversation,
  VoiceRecord,
} from "./voice-api";

export type SourceKind = "upload" | "conversation" | "document";
export type ExtractionMode = "knowledge" | "documents";

/** A queued stage for a source; conflict runs separately after extraction. */
export interface SourceJob {
  id: string;
  stage: "convert" | "extract" | "conflict";
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

/** The earlier source whose identical content was already extracted. */
export interface DuplicateOf {
  run_id: string;
  evidence_id: string;
  /** "running" while that extraction is still in progress. */
  status: "running" | "done";
  mode: ExtractionMode;
  name: string;
  kind: "upload" | "conversation";
  source_id: string;
  /** False when it was extracted with older extraction logic; null if unknown. */
  same_logic: boolean | null;
  extracted_at: string;
}

export type OverlapLevel = "near_duplicate" | "contained" | "similar";

/**
 * An extracted source in the same KB and mode whose text overlaps this one,
 * measured on 5-word shingles. `near_duplicate` and `contained` need the
 * reviewer's confirmation before extraction; `similar` is information only.
 */
export interface OverlapMatch extends Omit<DuplicateOf, "same_logic"> {
  level: OverlapLevel;
  /** Shared shingles / all shingles of both texts. */
  jaccard: number;
  /** Share of this text found in the earlier source. */
  containment: number;
  /** Share of the earlier source found in this text. */
  coverage: number;
}

export type ProcessingState = Omit<IngestionStatus, "status"> & {
  /**
   * "unavailable" when the evidence row could not be read; "duplicate" when
   * the worker skipped content that another source already extracted;
   * "near_duplicate" when it waits for the reviewer to confirm extraction of
   * content that nearly matches an extracted source.
   */
  status?: JobState | "unavailable" | "duplicate" | "near_duplicate";
  mode?: ExtractionMode;
  job?: SourceJob;
  conflict_job?: SourceJob;
  conflicts?: {
    status: "done";
    job_id: string;
    finished_at: string;
    open?: number;
    pending?: number;
    needs_review?: number;
    judged?: number;
    truncated?: boolean;
  };
  duplicate_of?: DuplicateOf;
  overlaps?: OverlapMatch[];
  /** The reviewer confirmed extraction despite a near-duplicate match. */
  overlap_confirmed?: boolean;
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
const originalDocumentUrl = (source: ManagedSource) =>
  `${sourcePath(source.kb, "upload", source.id)}/original`;

export async function downloadOriginalDocument(source: ManagedSource) {
  const response = await sessionClient.authorizedFetch(
    originalDocumentUrl(source),
  );
  if (!response.ok)
    throw new Error(`Không tải được file gốc (HTTP ${response.status}).`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = source.name;
  link.click();
  URL.revokeObjectURL(url);
}

/** Extraction refused: the same content was already extracted in this mode. */
export class DuplicateContentError extends Error {
  constructor(
    message: string,
    readonly duplicateOf: DuplicateOf,
  ) {
    super(message);
    this.name = "DuplicateContentError";
  }
}

/** Extraction waits for confirmation: the content nearly matches an extracted source. */
export class NearDuplicateError extends Error {
  constructor(
    message: string,
    readonly matches: OverlapMatch[],
  ) {
    super(message);
    this.name = "NearDuplicateError";
  }
}

async function duplicateError(response: Response) {
  try {
    const body = (await response.json()) as {
      detail?: {
        code?: string;
        message?: string;
        duplicate_of?: DuplicateOf;
        matches?: OverlapMatch[];
      };
    };
    const detail = body.detail;
    if (detail?.code === "duplicate_content" && detail.duplicate_of)
      return new DuplicateContentError(
        detail.message ?? "Nội dung trùng với một nguồn đã trích xuất.",
        detail.duplicate_of,
      );
    if (detail?.code === "near_duplicate" && detail.matches?.length)
      return new NearDuplicateError(
        detail.message ??
          "Nội dung gần giống một nguồn đã trích xuất. Xác nhận nếu vẫn muốn trích xuất.",
        detail.matches,
      );
  } catch {
    // Not JSON: fall back to the generic conflict message.
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await sessionClient.authorizedFetch(path, init);
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
    if (response.status === 409) {
      const duplicate = await duplicateError(response);
      if (duplicate) throw duplicate;
      throw new Error(
        "Nguồn đang được xử lý hoặc đã có kết quả. Hãy làm mới để xem trạng thái.",
      );
    }
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
  options: { confirmOverlap?: boolean } = {},
) =>
  request<{
    status: "queued";
    mode: ExtractionMode;
    job: SourceJob;
    /** Overlaps that did not block: `similar` ones, or confirmed matches. */
    overlaps?: OverlapMatch[];
  }>(`${sourcePath(kb, kind, id)}/extract`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(
      options.confirmOverlap ? { mode, confirm_overlap: true } : { mode },
    ),
  });

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
    overlaps: undefined,
    overlap_confirmed: undefined,
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
