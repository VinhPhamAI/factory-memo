import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ConversationError,
  ConversationsApi,
  type StreamEvent,
} from "./conversations-api";

const conversation = {
  id: "0190-chat",
  title: "Máy ép số 3",
  created_at: "2026-11-03T08:00:00Z",
  updated_at: "2026-11-03T08:00:00Z",
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

/** A streamed body whose chunks split SSE frames at arbitrary points. */
const sse = (...chunks: string[]) =>
  new Response(
    new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );

function fakeAuth(...responses: (() => Response)[]) {
  const fetch = vi.fn<
    (url: string, init?: RequestInit) => Promise<Response>
  >(async () => {
    const next = responses.shift();
    if (!next) throw new Error("Unexpected request");
    return next();
  });
  const refresh = vi.fn(async () => true);
  return { auth: { authorizedFetch: fetch, refresh }, fetch, refresh };
}

async function collect(events: AsyncIterable<StreamEvent>) {
  const seen: StreamEvent[] = [];
  for await (const event of events) seen.push(event);
  return seen;
}

afterEach(() => vi.restoreAllMocks());

describe("ConversationsApi", () => {
  it("lists, creates, renames and deletes through the backend", async () => {
    const { auth, fetch } = fakeAuth(
      () => json({ conversations: [conversation] }),
      () => json(conversation, 201),
      () => json({ ...conversation, title: "E12" }),
      () => new Response(null, { status: 204 }),
    );
    const api = new ConversationsApi(auth, "");

    expect(await api.list()).toEqual([conversation]);
    expect(await api.create("Máy ép số 3")).toEqual(conversation);
    expect((await api.rename(conversation.id, "E12")).title).toBe("E12");
    await api.remove(conversation.id);

    const calls = fetch.mock.calls.map(([url, init]) => [
      init?.method ?? "GET",
      url,
    ]);
    expect(calls).toEqual([
      ["GET", "/v1/conversations"],
      ["POST", "/v1/conversations"],
      ["PATCH", "/v1/conversations/0190-chat"],
      ["DELETE", "/v1/conversations/0190-chat"],
    ]);
    expect(JSON.parse(fetch.mock.calls[1][1]!.body as string)).toEqual({
      title: "Máy ép số 3",
    });
  });

  it("returns saved history with its truncation flag", async () => {
    const history = {
      conversation_id: conversation.id,
      messages: [
        { role: "user", text: "Lỗi E12" },
        { role: "assistant", text: "Kiểm tra cảm biến" },
      ],
      truncated: true,
    };
    const { auth } = fakeAuth(() => json(history));
    expect(await new ConversationsApi(auth, "").history(conversation.id)).toEqual(
      history,
    );
  });

  it("decodes provisional text and the saved answer across split chunks", async () => {
    const { auth, fetch } = fakeAuth(() =>
      sse(
        'event: status\ndata: {"sequence":1,"state":"generating"}\n\nevent: te',
        'xt\ndata: {"sequence":2,"delta":"Kiểm"}\r\n\r\nevent: text\ndata: {"seq',
        'uence":3,"delta":" tra"}\n\n',
        'event: complete\ndata: {"sequence":4,"finish_reason":"stop","answer":"Kiểm tra cảm biến."}\n\n',
      ),
    );
    const events = await collect(
      new ConversationsApi(auth, "").stream(conversation.id, "Lỗi E12"),
    );

    expect(events).toEqual([
      { type: "status" },
      { type: "text", delta: "Kiểm" },
      { type: "text", delta: " tra" },
      { type: "complete", answer: "Kiểm tra cảm biến." },
    ]);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/v1/conversations/0190-chat/stream");
    expect(JSON.parse(init!.body as string)).toEqual({ message: "Lỗi E12" });
  });

  it("reports an agent error event", async () => {
    const { auth } = fakeAuth(() =>
      sse(
        'event: text\ndata: {"delta":"partial"}\n\n',
        'event: error\ndata: {"code":"timeout","message":"The answer exceeded the request deadline.","retryable":true}\n\n',
      ),
    );
    const events = await collect(
      new ConversationsApi(auth, "").stream(conversation.id, "x"),
    );
    expect(events.at(-1)).toEqual({
      type: "error",
      code: "timeout",
      message: "The answer exceeded the request deadline.",
      retryable: true,
    });
  });

  it("marks a stream that ends without a final event as interrupted", async () => {
    const { auth } = fakeAuth(() =>
      sse('event: text\ndata: {"delta":"partial"}\n\nevent: complete\ndata: {"ans'),
    );
    const events = await collect(
      new ConversationsApi(auth, "").stream(conversation.id, "x"),
    );
    expect(events).toEqual([
      { type: "text", delta: "partial" },
      { type: "interrupted" },
    ]);
  });

  it("refreshes an expired access token once before giving up", async () => {
    const { auth, fetch, refresh } = fakeAuth(
      () => json({ detail: "Not authenticated" }, 401),
      () => sse('event: complete\ndata: {"answer":"ok"}\n\n'),
    );
    const events = await collect(
      new ConversationsApi(auth, "").stream(conversation.id, "x"),
    );
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(events).toEqual([{ type: "complete", answer: "ok" }]);
  });

  it("turns rejected requests into typed errors without retrying", async () => {
    const { auth, fetch } = fakeAuth(
      () =>
        json(
          {
            detail: {
              code: "conversation_busy",
              message: "Conversation already has an active request.",
            },
          },
          409,
        ),
      () => json({ detail: "Conversation not found" }, 404),
      () => json({ detail: "The agent is temporarily unavailable" }, 503),
    );
    const api = new ConversationsApi(auth, "");
    for (const status of [409, 404, 503]) {
      const error = await collect(api.stream(conversation.id, "x")).catch(
        (cause: unknown) => cause,
      );
      expect(error).toBeInstanceOf(ConversationError);
      expect((error as ConversationError).status).toBe(status);
    }
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
