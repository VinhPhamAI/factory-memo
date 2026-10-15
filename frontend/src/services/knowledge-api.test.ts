import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiKnowledgeClient, listKnowledgeBases } from "./knowledge-api";
import type { AnswerEvent } from "@/types";

const envelope = (type: string, data = {}) => ({ version: 1, type, data });
const frame = (type: string, data = {}) =>
  `event: ${type}\r\ndata: ${JSON.stringify(envelope(type, data))}\r\n\r\n`;
const context = { kb: "maintenance", threadId: "thread-one" };

function stream(text: string, byteSize = 3) {
  const bytes = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream({
      start(controller) {
        for (let i = 0; i < bytes.length; i += byteSize)
          controller.enqueue(bytes.slice(i, i + byteSize));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream; charset=utf-8" } },
  );
}

async function collect(
  response: Response,
  signal = new AbortController().signal,
) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  const events: AnswerEvent[] = [];
  for await (const event of new ApiKnowledgeClient().streamAnswer(
    "Câu hỏi",
    [],
    signal,
    context,
  ))
    events.push(event);
  return events;
}

afterEach(() => vi.unstubAllGlobals());

describe("Knowledge-agent HTTP transport", () => {
  it("retains typed case card metadata from SSE without arbitrary context", async () => {
    const card = {
      context: "Line 5",
      observation: "Alarm",
      reasoning: "Suspected welder",
      action: "Planned checks",
      outcome: null,
      evidence: [{ turn_id: "T03", quote: "Check first." }],
    };
    const events = await collect(
      stream(
        frame("answer.citations", {
          citations: [
            {
              knowledge_id: "case",
              type: "case",
              claim: "Flattened text",
              case_card: card,
              scope: { machine_id: "AX205", secret: "omit" },
              status: "draft",
            },
          ],
        }) + frame("run.completed"),
      ),
    );
    expect(events[0]).toMatchObject({
      event: "answer.citations",
      citations: [
        {
          caseCard: card,
          reviewStatus: "draft",
          scope: { machine_id: "AX205" },
        },
      ],
    });
    expect(JSON.stringify(events)).not.toContain("secret");
  });
  it("loads and validates the backend KB list, without fixture fallbacks", async () => {
    const items = [{ name: "maintenance", description: "Bảo trì" }];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(Response.json({ knowledge_bases: items })),
    );
    expect(await listKnowledgeBases(new AbortController().signal)).toEqual(
      items,
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/knowledge-agent/knowledge-bases"),
      expect.anything(),
    );
    vi.mocked(fetch).mockResolvedValueOnce(
      Response.json({ knowledge_bases: [{ name: 42 }] }),
    );
    await expect(
      listKnowledgeBases(new AbortController().signal),
    ).rejects.toThrow("không hợp lệ");
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response("unavailable", { status: 503 }),
    );
    await expect(
      listKnowledgeBases(new AbortController().signal),
    ).rejects.toThrow("HTTP 503");
  });

  it("handles split UTF-8, CRLF, comments, multiline data, resets and both citation kinds", async () => {
    const text =
      ": heartbeat\r\n\r\n" +
      frame("run.started") +
      frame("tool.started", {
        phase: "reading",
        input: { secret: "do-not-render" },
      }) +
      frame("answer.delta", { text: "Bản tạm" }) +
      frame("answer.reset") +
      `data: {"version":1,\r\ndata: "type":"answer.delta","data":{"text":"Tiếng Việt [K:k1] [D:d1]"}}\r\n\r\n` +
      frame("answer.citations", {
        citations: [
          {
            knowledge_id: "k1",
            subject: "Motor",
            claim: "Nghi vấn",
            evidence_id: "source-1",
            stated_by: null,
            type: "case",
          },
          { doc_id: "d1", title: "Manual" },
        ],
      }) +
      frame("run.completed");
    const events = await collect(stream(text, 1));
    expect(events.filter((item) => item.event === "answer.delta")).toEqual([
      { event: "answer.delta", text: "Bản tạm" },
      { event: "answer.delta", text: "Tiếng Việt [K:k1] [D:d1]" },
    ]);
    expect(events).toContainEqual({ event: "answer.reset" });
    expect(events).toContainEqual({
      event: "answer.citations",
      citations: [
        expect.objectContaining({
          marker: "K:k1",
          kind: "knowledge",
          excerpt: "Nghi vấn",
          evidenceId: "source-1",
        }),
        expect.objectContaining({
          marker: "D:d1",
          kind: "document",
          excerpt: "",
          title: "Manual",
        }),
      ],
    });
    expect(JSON.stringify(events)).not.toContain("do-not-render");
    expect(events.at(-1)?.event).toBe("run.completed");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/maintenance/stream"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "Câu hỏi", thread_id: context.threadId }),
      }),
    );
  });

  it("reports HTTP errors, bad streams, agent failures and early EOF", async () => {
    for (const [response, error] of [
      [new Response("unavailable", { status: 503 }), "HTTP 503"],
      [new Response("HTML"), "không trả về agent stream"],
      [stream(frame("answer.delta", { text: "Partial" })), "bị ngắt"],
      [
        stream(
          frame("run.failed", { message: "Agent timeout", reason: "private" }),
        ),
        "Agent timeout",
      ],
      [stream("data: invalid-json\n\n"), "JSON"],
    ] as const)
      await expect(collect(response)).rejects.toThrow(error);
  });

  it("cancels the reader when stopped, without completing the answer", async () => {
    const abort = new AbortController();
    const cancel = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode(
                  frame("answer.delta", { text: "Partial" }),
                ),
              );
            },
            cancel,
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );
    const events: string[] = [];
    await expect(
      (async () => {
        for await (const event of new ApiKnowledgeClient().streamAnswer(
          "q",
          [],
          abort.signal,
          context,
        )) {
          events.push(event.event);
          abort.abort();
        }
      })(),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(events).toEqual(["answer.delta"]);
    expect(cancel).toHaveBeenCalledOnce();
  });
});
