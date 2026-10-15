import type { CaseCardData, Citation } from "@/types";

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export function parseCaseCard(value: unknown): CaseCardData | undefined {
  if (
    !isObject(value) ||
    !["context", "observation", "reasoning", "action"].every(
      (key) => typeof value[key] === "string",
    ) ||
    !(value.outcome === null || typeof value.outcome === "string")
  )
    return;
  return {
    context: value.context as string,
    observation: value.observation as string,
    reasoning: value.reasoning as string,
    action: value.action as string,
    outcome: value.outcome as string | null,
    evidence: Array.isArray(value.evidence)
      ? value.evidence
          .filter(
            (item): item is CaseCardData["evidence"][number] =>
              isObject(item) &&
              typeof item.turn_id === "string" &&
              /^T\d{2,}$/.test(item.turn_id) &&
              typeof item.quote === "string" &&
              !!item.quote.trim(),
          )
          .map(({ turn_id, quote }) => ({ turn_id, quote }))
      : [],
  };
}

/** Compatibility with saved citations produced by render_case_claim().
 * Only accept the complete, ordered format; never guess missing facets.
 */
export function caseCardFor(citation: Citation): CaseCardData | undefined {
  if (citation.kind !== "knowledge" || citation.recordType !== "case") return;
  const structured = parseCaseCard(citation.caseCard);
  if (structured) return structured;
  const text = citation.excerpt.replace(/\r\n/g, "\n");
  const sections = [
    ...text.matchAll(/^(Observation|Reasoning|Action|Outcome):[ \t]*/gm),
  ];
  if (
    sections.map((match) => match[1]).join(",") !==
    "Observation,Reasoning,Action,Outcome"
  )
    return;
  const values = sections.map((match, index) =>
    text
      .slice(
        match.index! + match[0].length,
        sections[index + 1]?.index ?? text.length,
      )
      .trim(),
  );
  return {
    context: text.slice(0, sections[0].index).trim(),
    observation: values[0],
    reasoning: values[1],
    action: values[2],
    outcome: values[3],
    evidence: [],
  };
}

export function parseScope(value: unknown): Record<string, string> | undefined {
  if (!isObject(value)) return;
  const allowed = [
    "machine_id",
    "machine_model",
    "machine_model_specific",
    "component_id",
    "component_model",
    "line_id",
    "station",
    "product_model",
    "configuration",
    "serial_range",
    "condition",
    "failure_mode",
  ];
  return Object.fromEntries(
    Object.entries(value).filter(
      ([key, item]) =>
        allowed.includes(key) && typeof item === "string" && item.trim(),
    ),
  ) as Record<string, string>;
}
