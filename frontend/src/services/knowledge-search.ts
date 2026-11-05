import { apiBaseUrl } from "./config";
import { sessionClient } from "./auth-api";
import type { SourceKind } from "./knowledge-management";
import type { LocatedReference } from "@/types";

export type SearchMode = "hybrid" | "dense" | "keyword";
export type ChunkKind = "knowledge" | "document_passage";
export type ChunkStatus = "draft" | "approved";

export interface SearchRequest {
  query: string;
  mode: SearchMode;
  kinds: ChunkKind[];
  statuses: ChunkStatus[];
  limit?: number;
}

export interface SearchEvidence {
  source_id: string;
  source_title: string;
  source_kind: string;
  references: LocatedReference[];
  /** Set when the source keeps its original file. */
  original_url: string | null;
}

export interface SearchResult {
  rank: number;
  chunk_id: string;
  citation: string;
  kind: ChunkKind;
  status: ChunkStatus;
  title: string;
  content: string;
  /** A score is null when its ranking did not produce the chunk. */
  scores: { fused: number | null; dense: number | null; keyword: number | null };
  evidence: SearchEvidence[];
}

export interface SearchResponse {
  query: string;
  mode: SearchMode;
  embedding_model: string | null;
  results: SearchResult[];
}

/** Evidence of a source with original bytes opens as an upload; the rest as text. */
export const evidenceKind = (evidence: SearchEvidence): SourceKind =>
  evidence.original_url ? "upload" : "conversation";

export async function searchChunks(
  kb: string,
  request: SearchRequest,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  let response: Response;
  try {
    response = await sessionClient.authorizedFetch(
      `${apiBaseUrl}/v1/knowledge-management/${encodeURIComponent(kb)}/search`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal,
      },
    );
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
    // The 409 message names the stored and configured embedding models.
    if (response.status === 409 && detail) throw new Error(detail);
    if (response.status === 404)
      throw new Error("Không tìm thấy knowledge base.");
    if (response.status === 422) throw new Error("Yêu cầu tìm kiếm không hợp lệ.");
    if (response.status === 503)
      throw new Error(
        "Dịch vụ tìm kiếm tạm thời không khả dụng. Chế độ từ khóa vẫn có thể dùng khi dịch vụ embedding không kết nối được.",
      );
    throw new Error(
      `Không tìm kiếm được (HTTP ${response.status}). Kiểm tra dịch vụ rồi thử lại.`,
    );
  }
  return response.json() as Promise<SearchResponse>;
}
