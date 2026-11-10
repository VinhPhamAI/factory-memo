import { apiBaseUrl } from "./config";
import type { TicketStage } from "./tickets";

export type InquiryStatus =
  "draft" | "open" | "needs_info" | "answered" | "closed" | (string & {});

/** An inquiry in a list (backend `/v1/inquiries`). */
export type InquirySummary = {
  id: string;
  title: string;
  status: InquiryStatus;
  requested_by: string;
  requester_name: string;
  assigned_to: string | null;
  assignee_name: string | null;
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
  /** Ticket board column, derived by the backend. */
  stage: TicketStage;
};

export type EvidenceItem = {
  kind: "citation" | "file";
  title: string;
  excerpt?: string | null;
  location?: string | null;
  evidence_id?: string | null;
  file_id?: string | null;
};

export type InquiryContext = {
  situation: string;
  observations: string;
  attempted_actions: string;
  proposed_fix: string;
  evidence: EvidenceItem[];
};

export type InquiryFile = {
  file_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  event_id: string | null;
};

export type EventKind = "accepted" | "edited" | "denied" | "question" | "reply";

export type InquiryEvent = {
  id: string;
  kind: EventKind | (string & {});
  content: string | null;
  actor: { id: string; display_name: string };
  created_at: string;
};

export type Inquiry = InquirySummary & {
  conversation_id: string;
  context: InquiryContext;
  files: InquiryFile[];
  events: InquiryEvent[];
};

export type Expert = { id: string; display_name: string };

/** A rejected inquiry request; `status` is the HTTP status (0 offline), `code` the server's error code. */
export class InquiryError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "InquiryError";
  }
}

/** The part of AuthClient this API needs. */
export interface InquiryAuth {
  authorizedFetch(input: string, init?: RequestInit): Promise<Response>;
  refresh(): Promise<boolean>;
}

const MESSAGES: Record<number, string> = {
  0: "Không kết nối được máy chủ.",
  401: "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.",
  404: "Không tìm thấy yêu cầu này.",
  409: "Trạng thái đã thay đổi",
  422: "Nội dung không hợp lệ.",
  429: "Trợ lý đang bận. Hãy thử lại sau ít phút.",
  502: "Trợ lý không soạn được yêu cầu. Hãy thử lại.",
  503: "Dịch vụ tạm thời không khả dụng. Hãy thử lại sau.",
};

const CODE_MESSAGES: Record<string, string> = {
  empty_conversation: "Cuộc trò chuyện chưa có nội dung để tạo yêu cầu.",
  draft_failed: "Trợ lý không soạn được yêu cầu. Hãy thử lại.",
  conversation_busy:
    "Cuộc trò chuyện đang có một câu trả lời khác. Hãy đợi rồi thử lại.",
  capacity_exceeded: "Trợ lý đang bận. Hãy thử lại sau ít phút.",
  ineligible_assignee: "Chuyên gia này không thể nhận yêu cầu.",
  unreadable_file: "Không đính kèm được file này.",
  content_required: "Hãy nhập nội dung.",
  invalid_transition: "Trạng thái đã thay đổi",
};

/** Pick a Vietnamese message from the server's error code, then the HTTP status. */
export function inquiryMessage(status: number, code?: string): string {
  return (
    (code && CODE_MESSAGES[code]) ||
    MESSAGES[status] ||
    "Không thực hiện được yêu cầu."
  );
}

const REVOKE_DELAY_MS = 60_000;
const enc = encodeURIComponent;

/**
 * Inquiry calls for the signed-in user. A 401 refreshes the access token once
 * and repeats the request; other failures are never retried.
 */
export class InquiriesApi {
  constructor(
    private readonly auth: InquiryAuth,
    private readonly baseUrl = apiBaseUrl,
  ) {}

  async list(box: "sent" | "assigned"): Promise<InquirySummary[]> {
    return (
      (await this.json(`/v1/inquiries?box=${box}`)) as {
        inquiries: InquirySummary[];
      }
    ).inquiries;
  }

  get(id: string): Promise<Inquiry> {
    return this.json(`/v1/inquiries/${enc(id)}`) as Promise<Inquiry>;
  }

  update(
    id: string,
    changes: { title?: string; context?: InquiryContext },
  ): Promise<Inquiry> {
    return this.json(`/v1/inquiries/${enc(id)}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    }) as Promise<Inquiry>;
  }

  async remove(id: string): Promise<void> {
    await this.request(`/v1/inquiries/${enc(id)}`, { method: "DELETE" });
  }

  async attachFile(id: string, fileId: string): Promise<void> {
    await this.request(`/v1/inquiries/${enc(id)}/files/${enc(fileId)}`, {
      method: "PUT",
    });
  }

  async detachFile(id: string, fileId: string): Promise<void> {
    await this.request(`/v1/inquiries/${enc(id)}/files/${enc(fileId)}`, {
      method: "DELETE",
    });
  }

  submit(id: string, assignedTo: string): Promise<Inquiry> {
    return this.json(`/v1/inquiries/${enc(id)}/submit`, {
      method: "POST",
      body: JSON.stringify({ assigned_to: assignedTo }),
    }) as Promise<Inquiry>;
  }

  addEvent(id: string, kind: EventKind, content?: string): Promise<Inquiry> {
    return this.json(`/v1/inquiries/${enc(id)}/events`, {
      method: "POST",
      body: JSON.stringify({ kind, content }),
    }) as Promise<Inquiry>;
  }

  /** Download an attached file through the authorized client; no token goes in the URL. */
  async downloadFile(id: string, file: InquiryFile): Promise<void> {
    const response = await this.request(
      `/v1/inquiries/${enc(id)}/files/${enc(file.file_id)}/content`,
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
      setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
    }
  }

  async listExperts(): Promise<Expert[]> {
    return ((await this.json("/v1/experts")) as { experts: Expert[] }).experts;
  }

  /** Add the canned demo AI proposal to a draft ticket (demo endpoint). */
  demoProposal(id: string): Promise<Inquiry> {
    return this.json(`/v1/inquiries/${enc(id)}/demo-proposal`, {
      method: "POST",
    }) as Promise<Inquiry>;
  }

  draftFromConversation(conversationId: string): Promise<Inquiry> {
    return this.json(`/v1/conversations/${enc(conversationId)}/inquiry-draft`, {
      method: "POST",
    }) as Promise<Inquiry>;
  }

  private async json(path: string, init: RequestInit = {}): Promise<unknown> {
    return (await this.request(path, init)).json();
  }

  private async request(
    path: string,
    init: RequestInit = {},
  ): Promise<Response> {
    const send = () =>
      this.auth.authorizedFetch(`${this.baseUrl}${path}`, {
        ...init,
        headers:
          typeof init.body === "string"
            ? { "Content-Type": "application/json" }
            : undefined,
      });
    let response: Response;
    try {
      response = await send();
      if (response.status === 401 && (await this.auth.refresh()))
        response = await send();
    } catch {
      throw new InquiryError(MESSAGES[0], 0);
    }
    if (response.ok) return response;
    let code: string | undefined;
    try {
      const body = (await response.json()) as { detail?: { code?: unknown } };
      if (typeof body.detail?.code === "string") code = body.detail.code;
    } catch {
      // Not a JSON error body; the status decides the message.
    }
    throw new InquiryError(
      inquiryMessage(response.status, code),
      response.status,
      code,
    );
  }
}

export type ReviewAction = "accept" | "edit" | "deny" | "question" | "reply";

/**
 * Review actions the user may take now. Mirrors the backend transition table:
 * the assignee acts on `open`; the requester replies on `needs_info`.
 * The backend still decides.
 */
export function allowedActions(
  inquiry: Pick<InquirySummary, "status" | "requested_by" | "assigned_to">,
  userId: string,
): ReviewAction[] {
  if (inquiry.status === "open" && inquiry.assigned_to === userId)
    return ["accept", "edit", "deny", "question"];
  if (inquiry.status === "needs_info" && inquiry.requested_by === userId)
    return ["reply"];
  return [];
}

export const STATUS_LABELS: Record<string, string> = {
  draft: "Bản nháp",
  open: "Đang chờ chuyên gia",
  needs_info: "Cần bổ sung",
  answered: "Đã trả lời",
  closed: "Đã từ chối",
};

export const EVENT_LABELS: Record<string, string> = {
  accepted: "Chấp nhận",
  edited: "Sửa giải pháp",
  denied: "Từ chối",
  question: "Hỏi thêm",
  reply: "Trả lời",
};
