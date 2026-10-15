import { describe, expect, it } from "vitest";
import {
  answerMarkdown,
  citationTitle,
  displayAnswer,
  numberedCitations,
} from "./citation-display";
import type { Citation } from "@/types";

const citations: Citation[] = [
  {
    id: "K:abc",
    marker: "K:abc",
    sourceId: "abc",
    number: 1,
    kind: "knowledge",
    title: "Ca bảo trì",
    excerpt: "Nghi vấn vòng bi",
  },
  {
    id: "D:manual",
    marker: "D:manual",
    sourceId: "manual",
    number: 2,
    kind: "document",
    title: "Manual hãng",
    excerpt: "",
  },
];

describe("Numbered source presentation", () => {
  it("numbers by first mention and opens the matching source without altering stored identifiers", () => {
    const content = "Xem [D:manual], rồi [K:ABC], đối chiếu lại [D:manual].";
    expect(
      numberedCitations(content, citations).map((item) => [
        item.sourceId,
        item.number,
      ]),
    ).toEqual([
      ["manual", 1],
      ["abc", 2],
    ]);
    expect(answerMarkdown(content, citations)).toBe(
      "Xem [1](#citation-1), rồi [2](#citation-2), đối chiếu lại [1](#citation-1).",
    );
    expect(displayAnswer(content, citations)).toBe(
      "Xem [1], rồi [2], đối chiếu lại [1].",
    );
    expect(citations[0]).toMatchObject({ marker: "K:abc", number: 1 });
  });

  it("does not flash raw IDs before citation metadata or an entire marker arrives", () => {
    for (const content of [
      "Câu trả lời [",
      "Câu trả lời [K",
      "Câu trả lời [K:",
      "Câu trả lời [K:ddc57399-bf46",
      "Câu trả lời [K:ddc57399-bf46]",
    ])
      expect(answerMarkdown(content, [], true)).toBe("Câu trả lời […]");
    expect(answerMarkdown("Không khớp [K:unknown]", citations)).toBe(
      "Không khớp [Nguồn chưa xác thực]",
    );
  });

  it("preserves the mock demo's numbered references and avoids ID title fallbacks", () => {
    const mock = [
      { id: "source", sourceId: "source", number: 1, excerpt: "Evidence" },
    ];
    expect(answerMarkdown("Trả lời [1].", mock)).toBe(
      "Trả lời [1](#citation-1).",
    );
    expect(citationTitle({ ...citations[0], title: "abc" })).toBe(
      "Tri thức tham chiếu",
    );
    expect(citationTitle(citations[1])).toBe("Manual hãng");
  });
});
