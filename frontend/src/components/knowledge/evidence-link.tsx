import { Link } from "react-router-dom";
import type { ChatSource } from "@/services/chat-tools";
import { sourceLink, type SourceKind } from "@/services/knowledge-management";
import type { LocatedReference } from "@/types";

function locationLabel(reference: LocatedReference): string {
  switch (reference.type) {
    case "page":
    case "image":
      return `trang ${reference.page}`;
    case "paragraph":
      return reference.table && reference.cell
        ? `bảng ${reference.table}, ô ${reference.cell}`
        : `đoạn ${reference.index}`;
    case "cells":
      return `${reference.sheet}!${reference.range}`;
    case "passage":
      return `đoạn ${reference.chunk_index}`;
    default:
      return "";
  }
}

/** Link to the authenticated knowledge source viewer and its evidence location. */
export function EvidenceLink({
  kb,
  sourceId,
  kind,
  title,
  located,
  testId,
}: {
  kb: string;
  sourceId: string;
  kind: SourceKind;
  title: string;
  located?: LocatedReference;
  testId?: string;
}) {
  const where = located ? locationLabel(located) : "";
  return (
    <Link
      className="vc-chip km-search-evidence"
      data-testid={testId}
      to={sourceLink({ kb, kind, id: sourceId })}
      state={located ? { located } : undefined}
    >
      {title}
      {where && ` · ${where}`}
    </Link>
  );
}

/** Show the same evidence links in chat result cards and citation previews. */
export function ChatSourceLinks({ sources }: { sources: ChatSource[] }) {
  if (!sources.length) return null;
  return (
    <div className="km-search-evidence-list" aria-label="Nguồn tri thức">
      {sources.map((source) => (
        <EvidenceLink
          key={`${source.kb}/${source.source_id}`}
          kb={source.kb}
          kind={source.source_kind}
          sourceId={source.source_id}
          title={source.title}
          located={source.reference}
        />
      ))}
    </div>
  );
}
