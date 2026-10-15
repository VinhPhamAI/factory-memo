import type { AnswerEvent, Citation, Source } from "@/types";
import {
  ANSWER_VI,
  CASE_ID,
  caseEvidence,
  FOLLOW_UP_QUOTE,
  MANUAL_QUOTE,
  SOURCE_IDS,
} from "@/fixtures/ax167";
import { sleep } from "./timing";
import { isLive } from "./config";
import { ApiKnowledgeClient } from "./knowledge-api";

export interface KnowledgeClient {
  streamAnswer(
    query: string,
    sources: Source[],
    signal: AbortSignal,
    context?: { kb: string; threadId: string },
  ): AsyncIterable<AnswerEvent>;
}

const fallback =
  "Chưa tìm thấy đủ thông tin trong nguồn của bản demo để trả lời câu hỏi này. Hãy hỏi về tiếng kêu của servo motor MR-JE-AX167 (Line 11, machining cell B).";

function matchesCase(query: string) {
  // Deliberately narrow fixture routing, not semantic search or a live model.
  const asset = /ax167|mr-je|servo|trục z|z-axis/i.test(query);
  const topic =
    /kêu|rít|ồn|mài|vòng bi|bạc đạn|noise|grind|bearing|tiếng/i.test(query);
  const otherAsset = (query.match(/MR-JE-[A-Z]*\d+/gi) ?? []).some(
    (name) => name.toUpperCase() !== "MR-JE-AX167",
  );
  return asset && topic && !otherAsset;
}

export function answerFor(
  query: string,
  sources: Source[],
): { text: string; citations: Citation[] } {
  if (!matchesCase(query)) return { text: fallback, citations: [] };
  const manual = sources.find((s) => s.id === SOURCE_IDS.manual);
  const followUp = sources.find((s) => s.id === SOURCE_IDS.followUp);
  const f05 = followUp?.turns?.find((turn) => turn.code === "F05");
  if (
    !manual?.text?.includes(MANUAL_QUOTE) ||
    !f05?.text.includes(FOLLOW_UP_QUOTE)
  )
    return { text: fallback, citations: [] };
  // Cảnh 6 shows the T10 case quote without its opening "Don't do that."
  const t10 = caseEvidence
    .find((item) => item.turnCode === "T10")!
    .quote.replace(/^Don't do that\. /, "");
  const citations: Citation[] = [
    {
      id: `${CASE_ID}:check`,
      number: 1,
      kind: "card",
      sourceId: CASE_ID,
      cardRow: "check",
      turnId: 10,
      turnCode: "T10",
      excerpt: t10,
    },
    {
      id: `${manual.id}:page:71`,
      number: 2,
      sourceId: manual.id,
      page: manual.pageStart,
      excerpt: MANUAL_QUOTE,
    },
    {
      id: `${followUp!.id}:turn:${f05.id}`,
      number: 3,
      sourceId: followUp!.id,
      turnId: f05.id,
      turnCode: f05.code,
      excerpt: FOLLOW_UP_QUOTE,
    },
  ];
  return { text: ANSWER_VI, citations };
}

export const CHAT_TIMING = { search: 1300, chunk: 55 };

export class MockKnowledgeClient implements KnowledgeClient {
  async *streamAnswer(
    query: string,
    sources: Source[],
    signal: AbortSignal,
  ): AsyncIterable<AnswerEvent> {
    const answer = answerFor(query, sources);
    await sleep(CHAT_TIMING.search, signal);
    yield { event: "answer.citations", citations: answer.citations };
    for (const text of answer.text.match(/\S+\s*/g) ?? []) {
      await sleep(CHAT_TIMING.chunk, signal);
      yield { event: "answer.delta", text };
    }
    yield { event: "run.completed" };
  }
}

export function createKnowledgeClient(): KnowledgeClient {
  return isLive ? new ApiKnowledgeClient() : new MockKnowledgeClient();
}
