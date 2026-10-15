import type { AnswerEvent, Citation, KnowledgeBase, Source } from "@/types";
import type { KnowledgeClient } from "./chat";
import { apiBaseUrl } from "./config";
import { parseCaseCard, parseScope } from "./case-card";

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export async function listKnowledgeBases(
  signal: AbortSignal,
): Promise<KnowledgeBase[]> {
  const response = await fetch(
    `${apiBaseUrl}/v1/knowledge-agent/knowledge-bases`,
    { signal },
  );
  if (!response.ok)
    throw new Error(`Không tải được knowledge base (HTTP ${response.status}).`);
  const body: unknown = await response.json();
  if (
    !object(body) ||
    !Array.isArray(body.knowledge_bases) ||
    !body.knowledge_bases.every(
      (kb) =>
        object(kb) &&
        typeof kb.name === "string" &&
        kb.name.trim() &&
        typeof kb.description === "string",
    )
  )
    throw new Error("Danh sách knowledge base không hợp lệ.");
  return body.knowledge_bases as KnowledgeBase[];
}

function citationsFrom(value: unknown): Citation[] {
  if (!Array.isArray(value)) throw new Error("Danh sách nguồn không hợp lệ.");
  return value.map((item, index) => {
    if (!object(item)) throw new Error("Nguồn trích dẫn không hợp lệ.");
    const knowledge = typeof item.knowledge_id === "string";
    const id = knowledge ? item.knowledge_id : item.doc_id;
    if (typeof id !== "string" || !id)
      throw new Error("Nguồn thiếu định danh.");
    const marker = `${knowledge ? "K" : "D"}:${id}`;
    return {
      id: marker,
      marker,
      number: index + 1,
      sourceId: id,
      kind: knowledge ? "knowledge" : "document",
      title:
        typeof item.subject === "string"
          ? item.subject
          : typeof item.title === "string"
            ? item.title
            : id,
      excerpt: typeof item.claim === "string" ? item.claim : "",
      evidenceId:
        typeof item.evidence_id === "string" ? item.evidence_id : undefined,
      statedBy: typeof item.stated_by === "string" ? item.stated_by : undefined,
      recordType: typeof item.type === "string" ? item.type : undefined,
      ...(knowledge && item.type === "case"
        ? {
            caseCard: parseCaseCard(item.case_card),
            scope: parseScope(item.scope),
            reviewStatus:
              item.status === "draft" ||
              item.status === "approved" ||
              item.status === "deprecated"
                ? item.status
                : undefined,
          }
        : {}),
    };
  });
}

function decodeEvent(frame: string): AnswerEvent | undefined {
  const lines = frame.split("\n");
  const data = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data) return;
  const envelope: unknown = JSON.parse(data);
  if (
    !object(envelope) ||
    envelope.version !== 1 ||
    typeof envelope.type !== "string" ||
    !object(envelope.data)
  )
    throw new Error("Định dạng agent stream không hợp lệ.");
  const payload = envelope.data;
  switch (envelope.type) {
    case "answer.delta":
      if (typeof payload.text !== "string")
        throw new Error("Nội dung trả lời không hợp lệ.");
      return { event: "answer.delta", text: payload.text };
    case "answer.reset":
      return { event: "answer.reset" };
    case "answer.citations":
      return {
        event: "answer.citations",
        citations: citationsFrom(payload.citations),
      };
    case "run.completed":
      return { event: "run.completed" };
    case "run.failed":
      throw new Error(
        typeof payload.message === "string"
          ? payload.message
          : "Agent không hoàn tất được câu trả lời.",
      );
    // Only show phase labels; never render arbitrary tool payloads or reasoning.
    case "run.started":
      return { event: "run.status", text: "Agent đang xử lý câu hỏi…" };
    case "tool.started":
      return {
        event: "run.status",
        text:
          payload.phase === "reading"
            ? "Đang đọc nguồn…"
            : "Đang tìm nguồn liên quan…",
      };
    case "tool.completed":
      return { event: "run.status", text: "Đang tổng hợp câu trả lời…" };
  }
}

/** POST SSE with incremental UTF-8 decoding, including CRLF split across chunks. */
export class ApiKnowledgeClient implements KnowledgeClient {
  async *streamAnswer(
    query: string,
    _sources: Source[],
    signal: AbortSignal,
    context?: { kb: string; threadId: string },
  ): AsyncIterable<AnswerEvent> {
    if (!context?.kb || !context.threadId)
      throw new Error("Hãy chọn knowledge base trước khi hỏi.");
    const response = await fetch(
      `${apiBaseUrl}/v1/knowledge-agent/${encodeURIComponent(context.kb)}/stream`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify({ query, thread_id: context.threadId }),
        signal,
      },
    );
    if (!response.ok)
      throw new Error(`Không gọi được QA agent (HTTP ${response.status}).`);
    if (
      !response.headers.get("content-type")?.includes("text/event-stream") ||
      !response.body
    )
      throw new Error("Backend không trả về agent stream.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;
    try {
      while (!completed) {
        signal.throwIfAborted();
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        buffer = buffer.replace(/\r\n/g, "\n");
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = decodeEvent(frame);
          if (event) {
            signal.throwIfAborted();
            if (event.event === "run.completed") completed = true;
            yield event;
            if (completed) break;
          }
        }
        if (done) break;
      }
      signal.throwIfAborted();
      if (!completed)
        throw new Error(
          "Kết nối agent bị ngắt trước khi hoàn tất. Hãy thử lại.",
        );
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
}
