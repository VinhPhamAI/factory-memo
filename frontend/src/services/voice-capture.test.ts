import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyTranscriptEvent,
  emptyTranscript,
  serverErrorMessage,
} from "@/hooks/voice-recording";
import { kbNameProblem, suggestKbName } from "./knowledge-base-admin";
import { encodeWav } from "./mic-recorder";
import type { VoiceRecord } from "./voice-api";
import {
  recordSocketUrl,
  typedTextProblem,
  uploadRecording,
} from "./voice-api";
import {
  claimText,
  clock,
  findQuote,
  pipelineSteps,
  recordEvidence,
  recordScope,
  recordSpeaker,
  speakerSlot,
} from "./voice-display";

const record = (context: Record<string, unknown>, claim = "Kiểm tra độ rơ.") =>
  ({
    id: "r1",
    subject: "trục Z",
    claim,
    type: "heuristic",
    status: "draft",
    context,
    stated_by: null,
    created_at: "2026-10-15T00:00:00Z",
    superseded_by: null,
    verify: null,
    consolidation: null,
    history: [],
  }) satisfies VoiceRecord;

describe("live transcript", () => {
  it("shows the server's turns and keeps the partial until text is added", () => {
    let state = applyTranscriptEvent(emptyTranscript, {
      type: "partial",
      text: "Trục",
    });
    state = applyTranscriptEvent(state, { type: "partial", text: " Z" });
    expect(state.partial).toBe("Trục Z");
    const first = {
      id: "T01",
      speaker: null,
      text: "Trục Z kêu rè.",
      start: 0.5,
      end: 2,
    };
    state = applyTranscriptEvent(state, { type: "turns", turns: [first] });
    expect(state).toEqual({ turns: [first], partial: "" });

    state = applyTranscriptEvent(state, { type: "partial", text: "Từ" });
    // A list that adds no text (only changed labels) keeps the partial.
    const labelled = { ...first, speaker: "A" };
    state = applyTranscriptEvent(state, { type: "turns", turns: [labelled] });
    expect(state).toEqual({ turns: [labelled], partial: "Từ" });
  });

  it("explains server errors and where the audio was kept", () => {
    expect(
      serverErrorMessage({ code: "empty", audio_file: "a.wav" }),
    ).toContain("File âm thanh vẫn được giữ: a.wav");
    expect(serverErrorMessage({ code: "unknown-code" })).toBe(
      "Máy chủ báo lỗi khi ghi âm.",
    );
  });
});

describe("WAV encoding", () => {
  it("writes a PCM16 mono header around the samples", async () => {
    const blob = encodeWav(
      [new Int16Array([1, -1]), new Int16Array([2])],
      16000,
    );
    const view = new DataView(await blob.arrayBuffer());
    expect(blob.size).toBe(44 + 6);
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(40, true)).toBe(6);
    expect(view.getInt16(46, true)).toBe(-1);
  });
});

describe("typed text", () => {
  it("explains text the server would refuse", () => {
    expect(typedTextProblem(" \n ", false)).toContain("Hãy nhập");
    expect(typedTextProblem("Ghi chú tự do", false)).toBe("");
    expect(typedTextProblem("Ghi chú tự do", true)).toContain("Tên: nội dung");
    expect(typedTextProblem("\nAnh Minh: Trục Z kêu.\nthêm", true)).toBe("");
    expect(typedTextProblem("[T01] Speaker A: x", true)).toBe("");
    expect(typedTextProblem("x".repeat(100_001), false)).toContain("quá dài");
  });
});

describe("knowledge base ids", () => {
  it("suggests a database-safe id from a Vietnamese title", () => {
    expect(suggestKbName("Bảo trì máy ép – Đà Nẵng")).toBe(
      "bao_tri_may_ep_da_nang",
    );
    expect(suggestKbName("  2026 Kaizen ")).toBe("kb_2026_kaizen");
    expect(suggestKbName("!!!")).toBe("");
    expect(suggestKbName("a".repeat(80))).toHaveLength(60);
  });

  it("explains ids the server would refuse", () => {
    expect(kbNameProblem("may_ep", ["general"])).toBe("");
    expect(kbNameProblem("general", ["general"])).toContain("đã được dùng");
    expect(kbNameProblem("May-ep", [])).toContain("chữ thường");
    expect(kbNameProblem("", [])).toContain("Nhập mã");
  });
});

describe("socket url", () => {
  it("follows the page origin and scheme", () => {
    expect(
      recordSocketUrl(
        "general",
        new URL("https://factory.local/capture") as unknown as Location,
      ),
    ).toBe("wss://factory.local/v1/voice/general/record");
    expect(
      recordSocketUrl(
        "general",
        new URL("http://127.0.0.1:5180/") as unknown as Location,
      ),
    ).toBe("ws://127.0.0.1:5180/v1/voice/general/record");
  });
});

describe("review display", () => {
  it("formats clocks and speaker colour slots", () => {
    expect(clock(65)).toBe("01:05");
    expect(clock(3725)).toBe("1:02:05");
    expect(clock(null)).toBe("00:00");
    expect([
      speakerSlot("A"),
      speakerSlot("B"),
      speakerSlot("G"),
      speakerSlot(null),
    ]).toEqual(["0", "1", "0", "unknown"]);
  });

  it("reads quotes, steps, scope and speaker from a record", () => {
    const item = record(
      {
        certainty: "uncertain",
        applies_to: "MR-JE-AX167",
        speaker: "Speaker B",
        steps: ["Tắt servo", "Đo độ rơ"],
        quotes: [{ turn_id: "T03", quote: "độ rơ" }, { quote: "" }],
      },
      "Quy trình đo.\n1. Tắt servo\n2. Đo độ rơ",
    );
    expect(recordEvidence(item)).toEqual([{ turnId: "T03", quote: "độ rơ" }]);
    expect(claimText(item)).toBe("Quy trình đo.");
    expect(recordScope(item)).toEqual([["Phạm vi áp dụng", "MR-JE-AX167"]]);
    expect(recordSpeaker(item)).toBe("B");
    expect(
      recordEvidence(
        record({ case_card: { evidence: [{ turn_id: "T01", quote: "rè" }] } }),
      ),
    ).toEqual([{ turnId: "T01", quote: "rè" }]);
  });

  it("finds quotes despite case and spacing differences", () => {
    const text = "Đã kiểm tra   Độ rơ 0,05 mm chưa?";
    expect(findQuote(text, "độ rơ 0,05")).toEqual([14, 24]);
    expect(findQuote(text, "kiểm tra độ")).not.toBeNull();
    expect(findQuote(text, "không có")).toBeNull();
  });

  it("maps the extraction job to pipeline steps", () => {
    const states = (status: Parameters<typeof pipelineSteps>[0]) =>
      pipelineSteps(status).map((step) => step.state);
    expect(states({ status: "queued" })).toEqual([
      "done",
      "waiting",
      "waiting",
      "waiting",
    ]);
    expect(states({ status: "running", stage: "verifying" })).toEqual([
      "done",
      "done",
      "active",
      "waiting",
    ]);
    expect(states({ status: "done" })).toEqual([
      "done",
      "done",
      "done",
      "done",
    ]);
    expect(states({ status: "failed" })[1]).toBe("failed");
  });
});

describe("recording upload fallback", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks for Auto extraction only when a mode is chosen", async () => {
    const forms: FormData[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        forms.push(init?.body as FormData);
        return Response.json({
          conversation_id: "c1",
          job: { id: "j1", stage: "extract", status: "queued" },
        });
      }),
    );
    const form = {
      name: "Ca đêm",
      clean: false,
      recordedAt: new Date("2026-10-23T00:00:00Z"),
      audio: new Blob(["wav"]),
    };
    const saved = await uploadRecording("general", {
      ...form,
      extractMode: "knowledge",
    });
    expect(saved.job?.stage).toBe("extract");
    await uploadRecording("general", form);
    expect(forms.map((data) => data.get("extract_mode"))).toEqual([
      "knowledge",
      null,
    ]);
    expect(forms.every((data) => data.get("extract") === "false")).toBe(true);
  });
});
