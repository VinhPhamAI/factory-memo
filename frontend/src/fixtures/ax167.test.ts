/// <reference types="node" />
// Every quote shown in the UI must match the repository source files verbatim.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ANSWER_VI,
  caseEvidence,
  cardRows,
  conversationTurns,
  documents,
  FOLLOW_UP_QUOTE,
  followUpTurns,
  MANUAL_QUOTE,
  ruledOut,
  SOURCE_IDS,
} from "./ax167";
import { answerFor } from "@/services/chat";
import { seedSources } from "./seed";

// Vitest runs from frontend/; the case files live at the repository root.
const read = (path: string) =>
  readFileSync(resolve(process.cwd(), "..", path), "utf8");
const collapse = (text: string) => text.replace(/\s+/g, " ").trim();

const caseJson = JSON.parse(read("case-mr-je-ax167.json"));
const transcript = read("conversation-mr-je-ax167.txt");
const script = read("docs/demo-script-mr-je-ax167.md");
const manual = read("benchmark/public/corpus_chunks.jsonl")
  .split("\n")
  .filter(Boolean)
  .map((line) => JSON.parse(line))
  .find((chunk) => chunk.chunk_id === SOURCE_IDS.manual);

describe("AX167 fixtures match the source files", () => {
  it("transcript turns are verbatim", () => {
    expect(conversationTurns).toHaveLength(14);
    for (const turn of conversationTurns)
      expect(transcript).toContain(
        `[${turn.code}] ${turn.speakerId} (${turn.role.replaceAll(" ", "_")}): ${turn.text}`,
      );
  });
  it("case evidence quotes equal the case JSON", () => {
    expect(
      caseEvidence.map(({ quote, turnCode }) => ({ quote, turn_id: turnCode })),
    ).toEqual(caseJson.context.case_card.evidence);
  });
  it("sample documents are the Phụ lục A–D blocks and the manual chunk", () => {
    expect(script).toContain(documents.faultReport);
    expect(script).toContain(documents.minutes);
    expect(script).toContain(documents.expertNote);
    expect(documents.faultReport).toContain("FR-L11-1007");
    expect(documents.manual.text).toBe(manual.text);
    expect(documents.manual.page_start).toBe(71);
    for (const turn of followUpTurns)
      expect(collapse(script)).toContain(collapse(turn.text));
  });
  it("card rows are the Cảnh 5 texts and every ref quote exists in its source", () => {
    const sources = seedSources();
    for (const row of cardRows) {
      expect(script).toContain(row.text);
      for (const ref of row.refs) {
        const source = sources.find((s) => s.id === ref.sourceId)!;
        const text = ref.turnId
          ? source.turns!.find((t) => t.id === ref.turnId)!.text
          : source.text!;
        expect(collapse(text)).toContain(collapse(ref.quote));
      }
    }
    for (const item of ruledOut)
      for (const ref of item.refs)
        expect(
          conversationTurns.find((t) => t.id === ref.turnId)!.text,
        ).toContain(ref.quote);
  });
  it("chat citations quote Cảnh 6 sources verbatim", () => {
    expect(script).toContain(MANUAL_QUOTE);
    expect(manual.text).toContain(MANUAL_QUOTE);
    expect(script).toContain(FOLLOW_UP_QUOTE);
    const answer = answerFor("MR-JE-AX167 trục Z kêu rít", seedSources());
    expect(answer.text).toBe(ANSWER_VI);
    expect(script).toContain(answer.citations[0].excerpt);
    // The answer keeps the exact values and names from the English answer.
    for (const value of ["50 mm/min", "15 phút", "Binh Duong", "MR-JE-AX167"])
      expect(ANSWER_VI).toContain(value);
  });
});
