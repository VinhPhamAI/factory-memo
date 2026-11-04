import { apiBaseUrl } from "./config";
import { sessionClient } from "./auth-api";

export const MAX_DOCUMENT_BYTES = 100 * 1024 * 1024;

export interface StoredDocument {
  id: string;
  kb: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  storage_key: string;
  uploaded_at: string;
  status: "stored";
  duplicate?: boolean;
  /** Present only when Auto queued conversion and extraction. */
  job?: { id: string; stage: string; status: string } | null;
  job_error?: string | null;
}

export async function uploadDocument(
  kb: string,
  file: File,
  extractMode?: "knowledge" | "documents" | null,
): Promise<StoredDocument> {
  if (!file.size) throw new Error("Tài liệu trống. Hãy chọn file có nội dung.");
  if (file.size > MAX_DOCUMENT_BYTES)
    throw new Error("Tài liệu quá lớn (tối đa 100 MiB mỗi file).");
  const data = new FormData();
  data.set("file", file);
  if (extractMode) data.set("extract_mode", extractMode);
  let response: Response;
  try {
    response = await sessionClient.authorizedFetch(
      `${apiBaseUrl}/v1/documents/${encodeURIComponent(kb)}/uploads`,
      { method: "POST", body: data },
    );
  } catch (error) {
    throw new Error("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.", {
      cause: error,
    });
  }
  if (response.ok) return (await response.json()) as StoredDocument;
  if (response.status === 413)
    throw new Error("Tài liệu quá lớn (tối đa 100 MiB mỗi file).");
  if (response.status === 404) {
    let detail: unknown;
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail;
    } catch {
      // Proxies may return HTML for a missing route.
    }
    throw new Error(
      detail === "Knowledge base not found"
        ? "Không tìm thấy knowledge base đã chọn."
        : "Không tìm thấy API tải tài liệu. Cần cập nhật backend hoặc kiểm tra cấu hình kết nối.",
    );
  }
  if (response.status === 503)
    throw new Error("Kho lưu trữ tài liệu chưa sẵn sàng. Hãy thử lại.");
  throw new Error(
    `Không tải được tài liệu (HTTP ${response.status}). Hãy thử lại.`,
  );
}
