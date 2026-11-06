import type { AuthClient } from "./auth-api";
import { apiBaseUrl } from "./config";

/** Largest accepted upload; the server enforces the same limit. */
export const MAX_FILE_BYTES = 100 * 1024 * 1024;
export const ACCEPTED_EXTENSIONS = [
  ".pdf",
  ".xlsx",
  ".docx",
  ".jpg",
  ".jpeg",
  ".png",
] as const;

/** Public metadata of a file in the signed-in user's library. */
export interface LibraryFile {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

/** A failed file library action; `status` is the HTTP status (0 for network errors). */
export class FileApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "FileApiError";
  }
}

type FileClient = Pick<AuthClient, "authorizedFetch" | "refresh">;

const REVOKE_DELAY_MS = 60_000;

const MESSAGES: Record<number, string> = {
  0: "Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.",
  400: "Yêu cầu tải lên không hợp lệ. Hãy tải từng file một.",
  401: "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.",
  404: "File không còn tồn tại.",
  411: "Trình duyệt không gửi kích thước file. Hãy thử trình duyệt khác.",
  413: "File quá lớn (tối đa 100 MiB).",
  415: "Chỉ nhận PDF, XLSX, DOCX, JPEG, PNG và nội dung phải đúng với đuôi file.",
  422: "File trống hoặc tên file không hợp lệ.",
  503: "Máy chủ tạm thời không xử lý được. Hãy thử lại sau.",
};

const messageFor = (status: number) =>
  MESSAGES[status] ?? `Thao tác thất bại (HTTP ${status}).`;

/**
 * Report why a file cannot be uploaded before sending it, or null when it
 * may be sent. The error carries the status the server would return. The
 * server still checks the type and the content.
 */
export function checkFile(file: File): FileApiError | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((extension) => name.endsWith(extension)))
    return new FileApiError(MESSAGES[415], 415);
  if (file.size === 0)
    return new FileApiError("File trống. Hãy chọn file có nội dung.", 422);
  if (file.size > MAX_FILE_BYTES) return new FileApiError(MESSAGES[413], 413);
  return null;
}

/**
 * Send a protected request. After a 401 the session is refreshed once and the
 * request is retried once; a failed refresh signs the tab out.
 */
async function send(
  client: FileClient,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const url = `${apiBaseUrl}${path}`;
  let response: Response;
  try {
    response = await client.authorizedFetch(url, init);
    if (response.status === 401 && (await client.refresh()))
      response = await client.authorizedFetch(url, init);
  } catch (error) {
    throw new FileApiError(MESSAGES[0], 0, { cause: error });
  }
  if (!response.ok)
    throw new FileApiError(messageFor(response.status), response.status);
  return response;
}

export async function listFiles(client: FileClient): Promise<LibraryFile[]> {
  const response = await send(client, "/v1/files");
  return ((await response.json()) as { files: LibraryFile[] }).files;
}

export async function uploadFile(
  client: FileClient,
  file: File,
): Promise<LibraryFile> {
  const problem = checkFile(file);
  if (problem) throw problem;
  const body = new FormData();
  body.set("file", file);
  const response = await send(client, "/v1/files", { method: "POST", body });
  return (await response.json()) as LibraryFile;
}

export async function deleteFile(
  client: FileClient,
  id: string,
): Promise<void> {
  await send(client, `/v1/files/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** Fetch a small, owner-only file preview as an image or text excerpt. */
export async function fetchFilePreview(client: FileClient, id: string, signal?: AbortSignal): Promise<Blob> {
  const response = await send(client, `/v1/files/${encodeURIComponent(id)}/preview`, { signal });
  return response.blob();
}

/** Open a PDF or image in a new tab; download Office formats instead. */
export async function openFile(client: FileClient, file: LibraryFile): Promise<void> {
  if (!["application/pdf", "image/png", "image/jpeg"].includes(file.content_type)) {
    return downloadFile(client, file);
  }
  const tab = window.open("", "_blank");
  if (!tab) throw new FileApiError("Trình duyệt đã chặn cửa sổ mới.", 0);
  tab.opener = null;
  try {
    const response = await send(client, `/v1/files/${encodeURIComponent(file.id)}/content`);
    const url = URL.createObjectURL(await response.blob());
    tab.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
  } catch (error) {
    tab.close();
    throw error;
  }
}

/**
 * Download a file through the authorized client and save it under its
 * original name. A plain link cannot send the bearer token.
 */
export async function downloadFile(
  client: FileClient,
  file: LibraryFile,
): Promise<void> {
  const response = await send(
    client,
    `/v1/files/${encodeURIComponent(file.id)}/content`,
  );
  const url = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = file.filename;
    document.body.append(link);
    link.click();
    link.remove();
  } finally {
    // Keep the URL alive long enough for every browser to start saving it.
    setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
  }
}
