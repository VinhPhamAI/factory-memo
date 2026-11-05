import { ChevronRight, Library, Search } from "lucide-react";
import { ChatSourceLinks } from "@/components/knowledge/evidence-link";
import type { ChatTool } from "@/services/chat-tools";
import type { Citation } from "@/services/conversations-api";
import { STATUS_LABELS } from "@/services/voice-display";

function toolStatus(call: ChatTool, streaming: boolean): string {
  const list = call.name === "list_knowledge_bases";
  switch (call.state) {
    case "running":
      return streaming ? "Đang thực hiện…" : "Đã gián đoạn";
    case "empty":
      return list ? "Không có kho khả dụng" : "Không có kết quả phù hợp";
    case "unavailable":
      return list ? "Không tải được danh sách" : "Không tra cứu được";
    case "results":
      return `${call.count ?? (list ? call.catalog.length : call.results.length)} ${list ? "kho" : "kết quả"}`;
  }
}

/** Render actual calls separately from the answer and streamed thinking. */
export function ChatToolCalls({
  calls,
  citations,
  streaming,
}: {
  calls: ChatTool[];
  citations: Citation[];
  streaming: boolean;
}) {
  const catalog = calls.find(
    (call) => call.name === "list_knowledge_bases",
  )?.catalog;
  return (
    <div className="chat-tool-calls">
      {calls.map((call) => {
        const list = call.name === "list_knowledge_bases";
        const kbTitle =
          catalog?.find((item) => item.name === call.kb)?.title ?? call.kb;
        const Icon = list ? Library : Search;
        return (
          <details
            key={call.id}
            className="chat-tool-call"
            data-testid="chat-tool-call"
          >
            <summary>
              <Icon size={15} aria-hidden="true" />
              <span className="chat-tool-summary">
                <strong>
                  {list ? "Danh sách kho tri thức" : `Tra cứu ${kbTitle}`}
                </strong>
                {call.query && (
                  <span className="chat-tool-query"> · “{call.query}”</span>
                )}
              </span>
              <span className="chat-tool-state">
                {toolStatus(call, streaming)}
              </span>
              <ChevronRight
                size={15}
                className="chat-tool-chevron"
                aria-hidden="true"
              />
            </summary>
            <div className="chat-tool-body">
              <code>{call.name}</code>
              {call.kb && (
                <dl className="chat-tool-arguments">
                  <dt>Kho tri thức</dt>
                  <dd>{call.kb}</dd>
                  <dt>Truy vấn</dt>
                  <dd>{call.query}</dd>
                </dl>
              )}
              {call.catalog.length > 0 && (
                <ul className="chat-tool-results">
                  {call.catalog.map((entry) => (
                    <li key={entry.name}>
                      <strong>{entry.title}</strong> <code>{entry.name}</code>
                      {entry.description && <p>{entry.description}</p>}
                    </li>
                  ))}
                </ul>
              )}
              {call.results.length > 0 && (
                <ol className="chat-tool-results">
                  {call.results.map((result) => {
                    const citation = citations.find(
                      (item) => item.evidence_id === result.chunk_id,
                    );
                    const excerpt = result.excerpt ?? citation?.excerpt;
                    return (
                      <li key={result.chunk_id}>
                        <header>
                          <span className="km-search-rank">#{result.rank}</span>
                          <span className={`vc-chip ${result.status}`}>
                            {STATUS_LABELS[result.status]}
                          </span>
                        </header>
                        <strong>{result.title}</strong>
                        {excerpt && <p>{excerpt}</p>}
                        <ChatSourceLinks sources={citation?.sources?.length ? citation.sources : result.sources} />
                      </li>
                    );
                  })}
                </ol>
              )}
              {call.truncated && (
                <p className="field-note">Chỉ hiển thị một phần kết quả.</p>
              )}
            </div>
          </details>
        );
      })}
    </div>
  );
}
