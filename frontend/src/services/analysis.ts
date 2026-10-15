import type { CardRow, KnowledgeCard, Source } from "@/types";
import {
  CASE_ID,
  CASE_SUBJECT,
  caseEvidence,
  cardRows,
  ruledOut,
  scopeChips,
  SOURCE_IDS,
} from "@/fixtures/ax167";

const collapse = (text: string) => text.replace(/\s+/g, " ").trim();

/** True when the quote appears in the cited source (turn text or document). */
export function quoteMatches(
  sources: Source[],
  sourceId: string,
  quote: string,
  turnId?: number,
) {
  const source = sources.find((item) => item.id === sourceId);
  if (!source) return false;
  const text = turnId
    ? source.turns?.find((turn) => turn.id === turnId)?.text
    : source.text;
  return !!text && collapse(text).includes(collapse(quote));
}

/**
 * Checks the eight evidence quotes of the extracted case against the saved
 * recording, the way the backend KnowledgeVerifier checks quotes.
 */
export function verifyCaseEvidence(sources: Source[]) {
  const results = caseEvidence.map((item) => ({
    ...item,
    ok: quoteMatches(
      sources,
      SOURCE_IDS.conversation,
      item.quote,
      Number(item.turnCode.slice(1)),
    ),
  }));
  return {
    results,
    matched: results.filter((item) => item.ok).length,
    total: results.length,
  };
}

/** Card rows limited to the evidence available in the selected inputs. */
export function rowsFor(selectedIds: string[]): CardRow[] {
  return cardRows.map((row) => ({
    ...row,
    refs: row.refs.filter((ref) => selectedIds.includes(ref.sourceId)),
  }));
}

export function buildCard(
  sources: Source[],
  selectedIds: string[],
): KnowledgeCard {
  const verified = verifyCaseEvidence(sources);
  return {
    id: CASE_ID,
    subject: CASE_SUBJECT,
    status: "draft",
    createdAt: new Date().toISOString(),
    scope: scopeChips,
    rows: rowsFor(selectedIds),
    ruledOut: ruledOut.map((item) => ({
      ...item,
      refs: item.refs.filter((ref) => selectedIds.includes(ref.sourceId)),
    })),
    sourceIds: selectedIds,
    verified: { matched: verified.matched, total: verified.total },
    reviewer: "expert_01",
    checklist: [false, false, false],
    comment: "",
  };
}

/** The card the rest of the demo cites, falling back to an unsaved draft. */
export function currentCard(cards: KnowledgeCard[]) {
  return cards.find((card) => card.id === CASE_ID);
}

// Fixed intervals (milliseconds at speed 1) for the simulated extraction.
export const ANALYSIS_TIMING = {
  intro: 700,
  search: 900,
  quote: 450,
  word: 26,
  settle: 450,
  verify: 170,
  assemble: 700,
};
