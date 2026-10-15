import type { Citation } from "@/types";

const markerKey = (marker: string) =>
  marker.startsWith("K:") ? marker.toLowerCase() : marker;
const markers = /\[(K:[^\]\n]+|D:[^\]\n]+)\]/g;

/** Number sources by first mention, retaining backend identifiers internally. */
export function numberedCitations(
  content: string,
  citations: Citation[],
): Citation[] {
  if (!citations.some((item) => item.marker)) return citations;
  const byMarker = new Map(
    citations
      .filter((item) => item.marker)
      .map((item) => [markerKey(item.marker!), item]),
  );
  const ordered = new Set<Citation>();
  for (const match of content.matchAll(markers)) {
    const citation = byMarker.get(markerKey(match[1]));
    if (citation) ordered.add(citation);
  }
  for (const citation of citations) ordered.add(citation);
  return [...ordered].map((citation, index) => ({
    ...citation,
    number: index + 1,
  }));
}

/** Raw answer is kept for provenance; only presentation/copy uses this text. */
export function displayAnswer(
  content: string,
  citations: Citation[],
  streaming = false,
): string {
  const numbered = numberedCitations(content, citations);
  const text = content.replace(markers, (_raw, marker: string) => {
    const citation = numbered.find(
      (item) => item.marker && markerKey(item.marker) === markerKey(marker),
    );
    return citation
      ? `[${citation.number}]`
      : streaming
        ? "[…]"
        : "[Nguồn chưa xác thực]";
  });
  // A marker can arrive over many deltas. Do not flash UUID fragments on screen.
  return streaming
    ? text.replace(/\[(?:[KD](?::[^\]\n]*)?)?$/, "[…]")
    : text.replace(/\[[KD]:[^\]\n]*(?=\n|$)/g, "[Nguồn chưa xác thực]");
}

export function answerMarkdown(
  content: string,
  citations: Citation[],
  streaming = false,
): string {
  const numbered = numberedCitations(content, citations);
  return displayAnswer(content, citations, streaming).replace(
    /\[(\d+)\]/g,
    (raw, number: string) =>
      numbered.some((item) => item.number === Number(number))
        ? `[${number}](#citation-${number})`
        : raw,
  );
}

export function citationTitle(citation: Citation): string {
  return citation.title &&
    citation.title !== citation.sourceId &&
    citation.title !== citation.marker
    ? citation.title
    : citation.kind === "knowledge"
      ? "Tri thức tham chiếu"
      : "Tài liệu tham chiếu";
}
