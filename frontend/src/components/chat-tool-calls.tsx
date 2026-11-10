import { ChevronRight, FilePenLine, Library, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { ChatSourceLinks } from "@/components/knowledge/evidence-link";
import type { ChatTool } from "@/services/chat-tools";
import type { Citation } from "@/services/conversations-api";
import { STATUS_LABELS } from "@/services/voice-display";

const INQUIRY_TOOLS = ["create_inquiry_draft", "update_inquiry_draft"];

function inquiryStatus(call: ChatTool): string {
  switch (call.state) {
    case "results":
      return call.name === "create_inquiry_draft"
        ? "Đã tạo nháp"
        : "Đã cập nhật nháp";
    case "needs_choice":
      return "Cuộc chat đã có nháp";
    case "not_draft":
      return "Yêu cầu đã được gửi, không sửa được";
    default:
      return "Không lưu được nháp";
  }
}

function toolStatus(call: ChatTool, streaming: boolean): string {
  const list = call.name === "list_knowledge_bases";
  if (call.state === "running")
    return streaming ? "Đang thực hiện…" : "Đã gián đoạn";
  if (INQUIRY_TOOLS.includes(call.name)) return inquiryStatus(call);
  switch (call.state) {
    case "empty":
      return list ? "Không có kho khả dụng" : "Không có kết quả phù hợp";
    case "unavailable":
      return list ? "Không tải được danh sách" : "Không tra cứu được";
    default:
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
        const inquiry = INQUIRY_TOOLS.includes(call.name);
        const Icon = list ? Library : inquiry ? FilePenLine : Search;
        const label = list
          ? "Danh sách kho tri thức"
          : call.name === "create_inquiry_draft"
            ? "Tạo nháp yêu cầu chuyên gia"
            : call.name === "update_inquiry_draft"
              ? "Sửa nháp yêu cầu chuyên gia"
              : `Tra cứu ${kbTitle}`;
        return (
          <details
            key={call.id}
            className="chat-tool-call"
            data-testid="chat-tool-call"
            // Inquiry cards start open so the draft link is visible.
            open={inquiry && call.state !== "running" ? true : undefined}
          >
            <summary>
              <Icon size={15} aria-hidden="true" />
              <span className="chat-tool-summary">
                <strong>{label}</strong>
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
              {inquiry && call.state !== "running" && (
                <div className="chat-tool-inquiry">
                  {call.title !== undefined && (
                    <p>
                      <strong>{call.title || "(chưa có tiêu đề)"}</strong>
                    </p>
                  )}
                  {call.inquiry_id && (
                    <Link to={`/tickets/${call.inquiry_id}`}>
                      {call.state === "results" &&
                      call.name === "create_inquiry_draft"
                        ? "Mở nháp để chọn chuyên gia và gửi"
                        : "Mở yêu cầu"}
                    </Link>
                  )}
                </div>
              )}
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
