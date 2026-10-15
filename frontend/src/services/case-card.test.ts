/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { caseCardFor, parseCaseCard, parseScope } from "./case-card";
import type { Citation } from "@/types";

const record = JSON.parse(
  readFileSync(resolve(process.cwd(), "..", "data/case-mr-je-ax167.json"), "utf8"),
);
const citation: Citation = {
  id: record.id,
  sourceId: record.id,
  number: 1,
  kind: "knowledge",
  recordType: "case",
  excerpt: record.claim,
};

describe("Case card presentation data", () => {
  it("keeps structured fields and exact turn quotes without inventing a completed outcome", () => {
    const card = parseCaseCard(record.context.case_card)!;
    expect(card).toEqual(record.context.case_card);
    expect(card.outcome).toBeNull();
    expect(caseCardFor({ ...citation, caseCard: card })).toEqual(card);
    const pending = {
      ...card,
      reasoning: "Welder suspected, not confirmed",
      action: "Planned checks only.",
      outcome: "Pending: report back before ordering parts.",
    };
    expect(caseCardFor({ ...citation, caseCard: pending })).toEqual(pending);
  });

  it("parses old saved claims only when all known section markers appear in order", () => {
    const card = caseCardFor(citation)!;
    expect(card.observation).toBe(record.context.case_card.observation);
    expect(card.action).toBe(record.context.case_card.action);
    expect(card.outcome).toBe("not stated");
    expect(card.evidence).toEqual([]);
    expect(
      caseCardFor({ ...citation, excerpt: "Unstructured case. Check first." }),
    ).toBeUndefined();
    expect(caseCardFor({ ...citation, recordType: "rule" })).toBeUndefined();
  });

  it("rejects malformed facets and strips arbitrary metadata", () => {
    expect(
      parseCaseCard({ ...record.context.case_card, action: 42 }),
    ).toBeUndefined();
    const card = parseCaseCard({
      ...record.context.case_card,
      secret: "omit",
      evidence: [
        { turn_id: "T03", quote: "Exact quote", secret: "omit" },
        { turn_id: "bad", quote: "Not a turn" },
      ],
    })!;
    expect(card.evidence).toEqual([{ turn_id: "T03", quote: "Exact quote" }]);
    expect(card).not.toHaveProperty("secret");
    expect(
      parseScope({
        machine_id: "M1",
        benchmark_run_id: "omit",
        secret: "omit",
      }),
    ).toEqual({ machine_id: "M1" });
  });
});
