import { apiBaseUrl } from "./config";

/** A conversation in the signed-in user's list (backend `/v1/conversations`). */
export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

/** Evidence that a marker such as [S1] in a saved answer refers to. */
export type Citation = {
  marker: string;
  evidence_id: string;
  title: string;
  excerpt: string;
  location: string | null;
};

/** not_used: no search; empty: nothing matched; unavailable: search failed. */
export type SearchStatus = "not_used" | "results" | "empty" | "unavailable";

export type HistoryMessage = {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  /** A photo saved with a user message; open it through the file service. */
  attachment?: { file_id: string; media_type: string } | null;
};

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
  | {
      type: "complete";
      answer: string;
      citations: Citation[];
      search: SearchStatus;
    }
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

const SPEECH_MESSAGES: Record<number, string> = {
  413: "Bản ghi quá dài. Hãy ghi tối đa 2 phút.",
  422: "Bản ghi âm không hợp lệ. Hãy ghi lại.",
  503: "Không nhận dạng được giọng nói lúc này. Hãy thử lại hoặc gõ câu hỏi.",
};

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const SEARCH_STATUSES: SearchStatus[] = [
  "not_used",
  "results",
  "empty",
  "unavailable",
];

/** Keep only well-formed citations; anything else is dropped, never guessed. */
export function parseCitations(value: unknown): Citation[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (item): item is Citation =>
        object(item) &&
        typeof item.marker === "string" &&
        typeof item.evidence_id === "string" &&
        typeof item.title === "string" &&
        typeof item.excerpt === "string" &&
        (item.location === null ||
          item.location === undefined ||
          typeof item.location === "string"),
    )
    .map((item) => ({ ...item, location: item.location ?? null }));
}

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
    return {
      type: "complete",
      answer: value.answer,
      citations: parseCitations(value.citations),
      search: SEARCH_STATUSES.includes(value.search as SearchStatus)
        ? (value.search as SearchStatus)
        : "not_used",
    };
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

  /**
   * Transcribe a recorded WAV clip (at most 2 minutes) to text the user can
   * edit. Speech failures get their own messages, not the agent ones.
   */
  async transcribe(wav: Blob, signal?: AbortSignal): Promise<string> {
    const form = new FormData();
    form.append("file", wav, "recording.wav");
    try {
      const body = (await this.json("/v1/speech/transcriptions", {
        method: "POST",
        body: form,
        signal,
      })) as { text?: unknown };
      return typeof body.text === "string" ? body.text : "";
    } catch (cause) {
      if (cause instanceof ConversationError && SPEECH_MESSAGES[cause.status])
        throw new ConversationError(
          SPEECH_MESSAGES[cause.status],
          cause.status,
        );
      throw cause;
    }
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
