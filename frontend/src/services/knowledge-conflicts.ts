import { apiBaseUrl } from "./config";
import type { VoiceRecord } from "./voice-api";
import type { SourceJob } from "./knowledge-management";

/**
 * Machine statuses come from the conflict job; confirmed, dismissed and
 * resolved are reviewer decisions that hold until either record's content changes.
 */
export type ConflictStatus =
  | "no_conflict"
  | "open"
  | "pending"
  | "needs_review"
  | "confirmed"
  | "dismissed"
  | "resolved";

export type ConflictRelation =
  | "contradiction"
  | "case_exception"
  | "scope_difference"
  | "refinement"
  | "equivalent"
  | "unrelated";

export type ConflictDecision = "confirm" | "dismiss" | "supersede";

export type ConflictSide =
  | (VoiceRecord & {
      missing?: false;
      evidence_id: string | null;
      valid_from?: string | null;
      valid_to?: string | null;
      /** The judge's verbatim quote from this record, when it found a conflict. */
      quote: string | null;
      superseded_by: string | null;
    })
  | { id: string; missing: true };

export interface ConflictReviewEvent {
  decision: ConflictDecision;
  status: ConflictStatus;
  note: string | null;
  actor: string;
  at: string;
  kept?: string;
  retired?: string;
}

export interface KnowledgeConflict {
  id: string;
  a_id: string;
  b_id: string;
  /** The record whose extraction found the pair. */
  new_id: string | null;
  status: ConflictStatus;
  relation: ConflictRelation | null;
  aspect: string | null;
  confidence: number | null;
  reason: string | null;
  quotes: Record<string, string>;
  /** Conflict links are written on the records. */
  linked: boolean;
  confirmed_by_pair: boolean;
  review: ConflictReviewEvent[];
  created_at: string;
  updated_at: string;
  /** A record changed after this judgement; re-run the comparison. */
  stale: boolean;
  a: ConflictSide;
  b: ConflictSide;
}

export interface ConflictPage {
  conflicts: KnowledgeConflict[];
  counts: Partial<Record<ConflictStatus, number>>;
  offset: number;
  limit: number;
  next_offset: number | null;
  /** False when the KB has no conflict detection stage. */
  enabled: boolean;
}

export interface RecheckResult {
  queued: SourceJob[];
  /** Sources that already have an active job. */
  busy: string[];
  sources: number;
  stale: number;
}

const base = (kb: string) =>
  `${apiBaseUrl}/v1/knowledge-management/${encodeURIComponent(kb)}/conflicts`;

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
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Not JSON: use the status message.
    }
    if (response.status === 404)
      throw new Error(
        detail === "Conflict not found"
          ? "Không tìm thấy cặp mâu thuẫn. Hãy làm mới danh sách."
          : "Knowledge base này chưa bật phát hiện mâu thuẫn hoặc API chưa được cập nhật.",
      );
    if (response.status === 409)
      throw new Error(
        detail.startsWith("A newer judgement")
          ? "Cặp này vừa được so sánh lại. Hãy làm mới danh sách."
          : detail.startsWith("This conflict was already resolved")
            ? "Cặp này đã được xử lý supersede."
            : detail.startsWith("The other record is already retired")
              ? "Bản ghi còn lại đã bị thay thế hoặc ngừng dùng."
              : detail.startsWith("A record of this pair")
                ? "Một bản ghi của cặp này không còn tồn tại."
                : "Không áp dụng được quyết định. Hãy làm mới danh sách.",
      );
    if (response.status === 422) throw new Error("Yêu cầu không hợp lệ.");
    throw new Error(
      `Không xử lý được yêu cầu (HTTP ${response.status}). Kiểm tra dịch vụ rồi thử lại.`,
    );
  }
  return response.json() as Promise<T>;
}

export function listConflicts(
  kb: string,
  options: {
    status?: ConflictStatus;
    evidenceId?: string;
    offset?: number;
  } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  if (options.status) params.set("status", options.status);
  if (options.evidenceId) params.set("evidence_id", options.evidenceId);
  if (options.offset) params.set("offset", String(options.offset));
  const query = params.toString();
  return request<ConflictPage>(`${base(kb)}${query ? `?${query}` : ""}`, {
    signal,
  });
}

export const reviewConflict = (
  kb: string,
  conflictId: string,
  decision: ConflictDecision,
  options: { note?: string; keepId?: string } = {},
) =>
  request<KnowledgeConflict>(
    `${base(kb)}/${encodeURIComponent(conflictId)}/review`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        decision,
        note: options.note ?? "",
        ...(decision === "supersede" ? { keep_id: options.keepId } : {}),
      }),
    },
  );

/** Queue the comparison again for the given records, or for every changed record. */
export const recheckConflicts = (kb: string, recordIds: string[] = []) =>
  request<RecheckResult>(`${base(kb)}/recheck`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ record_ids: recordIds }),
  });
