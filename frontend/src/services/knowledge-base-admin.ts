import type { KnowledgeBase } from "@/types";
import { ApiError } from "./voice-api";
import { apiBaseUrl } from "./config";
import { sessionClient } from "./auth-api";

export const MAX_KB_TITLE = 80;
export const MAX_KB_DESCRIPTION = 500;
const MAX_KB_NAME = 60;
const NAME_PATTERN = /^[a-z][a-z0-9_]*$/;

/** Database-safe id suggested from a title: "Bảo trì máy ép" -> "bao_tri_may_ep". */
export function suggestKbName(title: string) {
  const ascii = title
    .replace(/[đĐ]/g, "d")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
  const slug = ascii.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const name = /^[a-z]/.test(slug) ? slug : slug ? `kb_${slug}` : "";
  return name.slice(0, MAX_KB_NAME).replace(/_+$/, "");
}

/** Vietnamese reason the id cannot be used; "" when it can. */
export function kbNameProblem(name: string, taken: readonly string[]) {
  if (!name) return "Nhập mã cho knowledge base.";
  if (name.length > MAX_KB_NAME) return `Mã tối đa ${MAX_KB_NAME} ký tự.`;
  if (!NAME_PATTERN.test(name))
    return "Mã chỉ gồm chữ thường không dấu, số và dấu gạch dưới, bắt đầu bằng chữ cái.";
  if (taken.includes(name)) return `Mã “${name}” đã được dùng.`;
  return "";
}

export async function createKnowledgeBase(form: {
  name: string;
  title: string;
  description: string;
}): Promise<KnowledgeBase> {
  let response: Response;
  try {
    response = await sessionClient.authorizedFetch(`${apiBaseUrl}/v1/knowledge-bases`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(form),
    });
  } catch (error) {
    throw new Error("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.", {
      cause: error,
    });
  }
  if (response.ok) return (await response.json()) as KnowledgeBase;
  let detail = "";
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // Not JSON; the status code is enough.
  }
  if (response.status === 409)
    throw new Error(`Mã “${form.name}” đã được dùng. Chọn mã khác.`);
  if (response.status === 403)
    throw new Error("Đã đạt số knowledge base tối đa có thể tạo.");
  if (response.status === 503)
    throw new Error("Kho ứng dụng chưa sẵn sàng. Hãy thử lại.");
  throw new ApiError(response.status, detail);
}
