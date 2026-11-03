import { apiBaseUrl } from "./config";

/** A conversation in the signed-in user's list (backend `/v1/conversations`). */
export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type HistoryMessage = { role: "user" | "assistant"; text: string };

/** Saved messages, oldest first; `truncated` means older ones were left out. */
export type ConversationHistory = {
  conversation_id: string;
  messages: HistoryMessage[];
  truncated: boolean;
};

/**
 * Agent stream events. `text` is provisional; `complete.answer` is the saved
 * answer that replaces it. `interrupted` means the stream ended without a
 * final event, so whether the answer was saved is unknown until history is
 * reopened.
 */
export type StreamEvent =
  | { type: "status" }
  | { type: "text"; delta: string }
  | { type: "complete"; answer: string }
  | { type: "error"; code: string; message: string; retryable: boolean }
  | { type: "interrupted" };

/** A rejected conversation request; `status` is the HTTP status (0 offline). */
export class ConversationError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ConversationError";
  }
}

/** The part of AuthClient this API needs. */
export interface ConversationAuth {
  authorizedFetch(input: string, init?: RequestInit): Promise<Response>;
  refresh(): Promise<boolean>;
}

const MESSAGES: Record<number, string> = {
  401: "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.",
  404: "Không tìm thấy cuộc trò chuyện này.",
  409: "Cuộc trò chuyện đang có một câu trả lời khác. Hãy đợi rồi thử lại.",
  422: "Nội dung gửi không hợp lệ.",
  429: "Trợ lý đang bận. Hãy thử lại sau ít phút.",
  503: "Trợ lý tạm thời không khả dụng. Hãy thử lại sau.",
};

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function parseEvent(name: string, data: string): StreamEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return null;
  }
  if (!object(value)) return null;
  if (name === "status") return { type: "status" };
  if (name === "text" && typeof value.delta === "string")
    return { type: "text", delta: value.delta };
  if (name === "complete" && typeof value.answer === "string")
    return { type: "complete", answer: value.answer };
  if (name === "error")
    return {
      type: "error",
      code: typeof value.code === "string" ? value.code : "error",
      message: typeof value.message === "string" ? value.message : "",
      retryable: value.retryable === true,
    };
  return null;
}

/** Split UTF-8 SSE bytes into events; a partial trailing frame is dropped. */
async function* readEvents(
  body: ReadableStream<Uint8Array>,
): AsyncIterable<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let name = "message";
        const data: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) name = line.slice(6).trim();
          else if (line.startsWith("data:"))
            data.push(line.slice(5).replace(/^ /, ""));
        }
        const event = parseEvent(name, data.join("\n"));
        if (!event) continue;
        yield event;
        if (event.type === "complete" || event.type === "error") {
          finished = true;
          return;
        }
      }
    }
  } finally {
    reader.releaseLock();
    if (finished) void body.cancel().catch(() => undefined);
  }
  yield { type: "interrupted" };
}

/**
 * Conversation list, history and answer streaming for the signed-in user.
 * A 401 refreshes the access token once and repeats the request; the backend
 * rejects 401 requests before they reach the agent, so this never sends a
 * message twice. Other failures are never retried.
 */
export class ConversationsApi {
  constructor(
    private readonly auth: ConversationAuth,
    private readonly baseUrl = apiBaseUrl,
  ) {}

  async list(signal?: AbortSignal): Promise<Conversation[]> {
    const body = (await this.json("/v1/conversations", { signal })) as {
      conversations: Conversation[];
    };
    return body.conversations;
  }

  create(title: string, signal?: AbortSignal): Promise<Conversation> {
    return this.json("/v1/conversations", {
      method: "POST",
      body: JSON.stringify({ title }),
      signal,
    }) as Promise<Conversation>;
  }

  rename(id: string, title: string): Promise<Conversation> {
    return this.json(`/v1/conversations/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }) as Promise<Conversation>;
  }

  async remove(id: string): Promise<void> {
    await this.request(`/v1/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  }

  history(id: string, signal?: AbortSignal): Promise<ConversationHistory> {
    return this.json(`/v1/conversations/${encodeURIComponent(id)}/history`, {
      signal,
    }) as Promise<ConversationHistory>;
  }

  /** Send one message and yield the agent's events until a final one. */
  async *stream(
    id: string,
    message: string,
    signal?: AbortSignal,
  ): AsyncIterable<StreamEvent> {
    const response = await this.request(
      `/v1/conversations/${encodeURIComponent(id)}/stream`,
      { method: "POST", body: JSON.stringify({ message }), signal },
    );
    if (!response.body) {
      yield { type: "interrupted" };
      return;
    }
    yield* readEvents(response.body);
  }

  private async json(path: string, init: RequestInit): Promise<unknown> {
    return (await this.request(path, init)).json();
  }

  private async request(path: string, init: RequestInit): Promise<Response> {
    const send = () =>
      this.auth.authorizedFetch(`${this.baseUrl}${path}`, {
        ...init,
        headers: init.body ? { "Content-Type": "application/json" } : undefined,
      });
    let response: Response;
    try {
      response = await send();
      if (response.status === 401 && (await this.auth.refresh()))
        response = await send();
    } catch (cause) {
      if (init.signal?.aborted) throw cause;
      throw new ConversationError("Không kết nối được máy chủ.", 0);
    }
    if (response.ok) return response;
    throw new ConversationError(
      MESSAGES[response.status] ?? "Không thực hiện được yêu cầu.",
      response.status,
    );
  }
}
