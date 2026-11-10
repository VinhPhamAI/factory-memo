import { useState } from "react";
import type { ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatSourceLinks } from "@/components/knowledge/evidence-link";
import type { Citation, SearchStatus } from "@/services/conversations-api";

const SEARCH_NOTES: Partial<Record<SearchStatus, string>> = {
  empty: "Không tìm thấy tri thức phù hợp trong kho.",
  unavailable: "Không tra cứu được kho tri thức cho câu hỏi này.",
};

// Same marker form the agent validates: S1..S999 without leading zeros.
const MARKER = /\[(S[1-9]\d{0,2})\]/g;

/** Fenced and inline code spans, which must keep their `[S1]` text as written. */
const CODE_SPAN = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/;

/** Markdown extensions answers rely on: tables, task lists, strikethrough. */
export const ANSWER_PLUGINS = [remarkGfm];

/** A table that scrolls sideways inside its own frame instead of widening the chat. */
export function AnswerTable({ children }: { children?: ReactNode }) {
  return (
    <div className="answer-table">
      <table>{children}</table>
    </div>
  );
}

/**
 * An agent answer whose [S1]-style markers open the cited evidence.
 *
 * Only markers saved as citations become buttons, and only links this
 * component generated (with a per-render secret prefix) are honoured: links
 * and images written in the answer itself are rendered as plain text, so
 * injected content cannot load remote URLs.
 */
export function CitedAnswer({
  text,
  citations,
  search,
}: {
  text: string;
  citations: Citation[];
  search?: SearchStatus;
}) {
  const [open, setOpen] = useState<string>();
  const [prefix] = useState(() => `#cite-${crypto.randomUUID()}-`);
  const byMarker = new Map(citations.map((item) => [item.marker, item]));
  const linked = text
    .split(CODE_SPAN)
    .map((part, index) =>
      index % 2
        ? part
        : part.replace(MARKER, (whole, marker: string) =>
            byMarker.has(marker) ? `[${whole}](${prefix}${marker})` : whole,
          ),
    )
    .join("");
  const selected = open ? byMarker.get(open) : undefined;
  const note = search ? SEARCH_NOTES[search] : undefined;
  return (
    <>
      <Markdown
        remarkPlugins={ANSWER_PLUGINS}
        urlTransform={(url) => (url.startsWith(prefix) ? url : "")}
        components={{
          img: () => null,
          table: AnswerTable,
          a: ({ href, children }) => {
            const marker = href?.startsWith(prefix)
              ? href.slice(prefix.length)
              : undefined;
            if (!marker || !byMarker.has(marker))
              return <span>{children}</span>;
            return (
              <button
                type="button"
                className="inline-citation"
                aria-label={`Mở nguồn ${marker}`}
                aria-expanded={open === marker}
                onClick={() =>
                  setOpen((current) =>
                    current === marker ? undefined : marker,
                  )
                }
              >
                [{marker}]
              </button>
            );
          },
        }}
      >
        {linked}
      </Markdown>
      {selected && (
        <div className="citation-card" data-testid="citation-card">
          <p className="citation-card-title">
            [{selected.marker}] {selected.title}
            {selected.location ? ` · ${selected.location}` : ""}
          </p>
          <blockquote>{selected.excerpt}</blockquote>
          <ChatSourceLinks sources={selected.sources ?? []} />
          <p className="field-note">
            Trích từ kết quả tìm kiếm; có thể là tri thức nháp chưa được duyệt.
          </p>
        </div>
      )}
      {note && <p className="field-note">{note}</p>}
    </>
  );
}
