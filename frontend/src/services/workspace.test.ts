/// <reference types="node" />
import { Blob as NodeBlob } from "node:buffer";
import { describe, expect, it } from "vitest";
import { WorkspaceStorage, storageError } from "./storage";
import { answerFor, MockKnowledgeClient } from "./chat";
import {
  MAX_FILE_SIZE,
  recordingSource,
  recordingView,
  TOTAL_TICKS,
  validateFile,
} from "./capture";
import { buildCard, verifyCaseEvidence } from "./analysis";
import { detectFields, ocrLines } from "./ocr";
import { getSpeed, scaled, setSpeed } from "./timing";
import { seedSources } from "@/fixtures/seed";
import {
  ANSWER_VI,
  CASE_ID,
  conversationTurns,
  SOURCE_IDS,
  SUGGESTED_QUESTION,
} from "@/fixtures/ax167";
import type { ChatThread } from "@/types";

describe("Simulated timing", () => {
  it("scales every delay by the speed factor", () => {
    expect(getSpeed()).toBe(1);
    expect(scaled(900)).toBe(900);
    setSpeed(20);
    expect(scaled(900)).toBe(45);
    setSpeed(0);
    expect(getSpeed()).toBe(1);
  });
});

describe("Simulated recording", () => {
  it("streams T01–T14 word by word with the active speaker", () => {
    expect(recordingView(0).turns).toHaveLength(0);
    const early = recordingView(15);
    expect(early.turns).toHaveLength(1);
    expect(early.turns[0].streaming).toBe(true);
    expect(early.active).toBe("junior_01");
    expect(conversationTurns[0].text.startsWith(early.turns[0].text)).toBe(
      true,
    );
    const end = recordingView(TOTAL_TICKS);
    expect(end.complete).toBe(true);
    expect(end.active).toBeUndefined();
    expect(end.completed.map((turn) => turn.text)).toEqual(
      conversationTurns.map((turn) => turn.text),
    );
    expect(end.completed.map((turn) => turn.code)).toEqual(
      Array.from(
        { length: 14 },
        (_, i) => `T${String(i + 1).padStart(2, "0")}`,
      ),
    );
  });
  it("saves only completed turns as a draft conversation", () => {
    const partial = recordingSource(40);
    expect(partial.status).toBe("draft");
    expect(partial.id).toBe(SOURCE_IDS.conversation);
    expect(partial.turns!.length).toBeLessThan(14);
    expect(partial.summary).toBe("");
    expect(recordingSource(TOTAL_TICKS).turns).toHaveLength(14);
  });
  it("validates allowed extensions, content and the 20 MB limit", () => {
    expect(validateFile(new File(["note"], "process.md"))).toBeUndefined();
    expect(validateFile(new File(["note"], "process.exe"))).toContain("PDF");
    expect(validateFile(new File([], "empty.txt"))).toContain("trống");
    expect(
      validateFile({ name: "huge.pdf", size: MAX_FILE_SIZE + 1 } as File),
    ).toContain("20 MB");
  });
});

describe("Simulated OCR", () => {
  it("detects machine, line, station, report code and date", () => {
    const report = seedSources().find((s) => s.id === SOURCE_IDS.faultReport)!;
    const fields = detectFields(report.text!);
    expect(fields.map((f) => f.value)).toEqual(
      expect.arrayContaining([
        "MR-JE-AX167",
        "Line 11",
        "Machining cell B",
        "FR-L11-1007",
        "2026-10-07",
      ]),
    );
    expect(ocrLines(report.text!)[0]).toContain("FAULT REPORT");
  });
});

describe("Extraction and verification", () => {
  it("matches 8/8 case quotes against the full recording and fewer against a partial one", () => {
    expect(verifyCaseEvidence(seedSources()).matched).toBe(8);
    const partial = seedSources().map((source) =>
      source.id === SOURCE_IDS.conversation ? recordingSource(200) : source,
    );
    expect(verifyCaseEvidence(partial).matched).toBeLessThan(8);
  });
  it("builds a draft card with seven rows and drops refs to unselected inputs", () => {
    const sources = seedSources();
    const card = buildCard(
      sources,
      sources.map((s) => s.id),
    );
    expect(card.id).toBe(CASE_ID);
    expect(card.status).toBe("draft");
    expect(card.rows).toHaveLength(7);
    expect(card.verified).toEqual({ matched: 8, total: 8 });
    const withoutManual = buildCard(
      sources,
      sources.map((s) => s.id).filter((id) => id !== SOURCE_IDS.manual),
    );
    expect(
      withoutManual.rows.flatMap((row) => row.refs).map((ref) => ref.sourceId),
    ).not.toContain(SOURCE_IDS.manual);
  });
});

describe("Evidence-grounded demo answers", () => {
  it("answers the suggested question in Vietnamese with three sources", () => {
    const answer = answerFor(SUGGESTED_QUESTION, seedSources());
    expect(answer.text).toBe(ANSWER_VI);
    expect(answer.citations.map((c) => c.number)).toEqual([1, 2, 3]);
    expect(answer.citations[0]).toMatchObject({
      kind: "card",
      sourceId: CASE_ID,
      cardRow: "check",
      turnCode: "T10",
    });
    for (const n of [1, 2, 3]) expect(answer.text).toContain(`[${n}]`);
  });
  it("abstains on unrelated questions and other assets", () => {
    for (const query of [
      "Giá một servo bao nhiêu?",
      "MR-JE-AX932 kêu rít khi chạy nhanh?",
      "How do I repair a furnace?",
    ])
      expect(answerFor(query, seedSources()).citations).toEqual([]);
  });
  it("reassembles the streamed answer and stops without a completion event", async () => {
    setSpeed(1000);
    const client = new MockKnowledgeClient();
    const controller = new AbortController();
    let text = "";
    const events: string[] = [];
    for await (const event of client.streamAnswer(
      SUGGESTED_QUESTION,
      seedSources(),
      controller.signal,
    )) {
      events.push(event.event);
      if (event.event === "answer.delta") text += event.text;
    }
    expect(text).toBe(ANSWER_VI);
    expect(events.at(-1)).toBe("run.completed");
    controller.abort();
    const stopped: string[] = [];
    await expect(
      (async () => {
        for await (const event of client.streamAnswer(
          SUGGESTED_QUESTION,
          seedSources(),
          controller.signal,
        ))
          stopped.push(event.event);
      })(),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(stopped).not.toContain("run.completed");
    setSpeed(1);
  });
});

describe("Browser persistence", () => {
  it("seeds once, keeps sources, cards and threads across reopen, and resets", async () => {
    const name = `test-${crypto.randomUUID()}`;
    const first = new WorkspaceStorage(name);
    const data = await first.load();
    expect(data.sources).toHaveLength(6);
    expect(data.cards).toHaveLength(0);
    const file = new NodeBlob(["Expert note"], {
      type: "text/plain",
    }) as unknown as Blob;
    await first.saveSource({ ...data.sources[1], id: "uploaded", file });
    const card = buildCard(
      data.sources,
      data.sources.map((s) => s.id),
    );
    await first.saveCard({
      ...card,
      status: "approved",
      approvedBy: "expert_01",
    });
    const thread: ChatThread = {
      id: "chat-one",
      title: "Question",
      updatedAt: "2026-10-10",
      messages: [
        {
          id: "a",
          role: "assistant",
          status: "streaming",
          content: "Partial answer",
          citations: [],
        },
      ],
    };
    await first.saveThread(thread);
    await first.close();
    const second = new WorkspaceStorage(name);
    const restored = await second.load();
    expect(restored.sources).toHaveLength(7);
    expect(restored.sources.find((s) => s.id === "uploaded")?.file?.size).toBe(
      file.size,
    );
    expect(restored.cards[0]).toMatchObject({
      id: CASE_ID,
      status: "approved",
      approvedBy: "expert_01",
    });
    expect(restored.threads[0].messages[0].status).toBe("stopped");
    const reset = await second.reset();
    expect(reset.sources).toHaveLength(6);
    expect(reset.cards).toHaveLength(0);
    expect(reset.threads).toHaveLength(0);
    await second.close();
  });
  it("reports storage quota errors as unsaved changes", () => {
    expect(
      storageError(new DOMException("full", "QuotaExceededError")),
    ).toContain("chưa được lưu");
    expect(storageError(new Error("denied"))).toContain("thử lại");
  });
});
