import { useEffect, useRef, useState } from "react";
import { Download, FileText, Mic } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation, Source } from "@/types";
import { timestamp } from "@/services/capture";
import { Button } from "@/components/ui/button";

export function SourceIcon({
  kind,
  size = 18,
}: {
  kind: Source["kind"];
  size?: number;
}) {
  return kind === "document" ? <FileText size={size} /> : <Mic size={size} />;
}

export function SampleBadge() {
  return (
    <span className="sample-badge" title="Tài liệu mẫu viết cho demo">
      Mẫu
    </span>
  );
}

export function SourcePreview({
  source,
  citation,
  compact = false,
}: {
  source: Source;
  citation?: Pick<Citation, "turnId" | "page" | "excerpt">;
  compact?: boolean;
}) {
  const [url, setUrl] = useState("");
  const highlighted = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!source.file) return;
    const objectUrl = URL.createObjectURL(source.file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [source.file]);
  useEffect(() => {
    if (citation?.turnId)
      highlighted.current?.scrollIntoView({
        block: "center",
        behavior: "instant",
      });
  }, [citation?.turnId, source.id]);

  if (source.kind === "conversation")
    return (
      <div
        className={`transcript ${compact ? "transcript-compact" : ""}`}
        data-testid="source-transcript"
      >
        {source.turns?.map((turn) => {
          const cited = turn.id === citation?.turnId;
          return (
            <div
              key={turn.id}
              ref={cited ? highlighted : undefined}
              className={`transcript-turn ${cited ? "is-cited" : ""}`}
              data-testid={`turn-${turn.code}`}
              data-cited={cited || undefined}
            >
              <div
                className={`speaker-avatar ${turn.speakerId === "expert_01" ? "expert" : ""}`}
              >
                {turn.speakerId === "expert_01" ? "CG" : "KT"}
              </div>
              <div className="turn-content">
                <div className="turn-meta">
                  <span className="turn-chip">{turn.code}</span>
                  <strong>
                    {turn.speaker} · {turn.speakerId}
                  </strong>
                  <span>{timestamp(turn.seconds)}</span>
                </div>
                <p>{turn.text}</p>
              </div>
            </div>
          );
        })}
        {!source.turns?.length && <p className="muted">Chưa có transcript.</p>}
      </div>
    );

  return (
    <div className="document-preview">
      <div className="document-meta">
        <span>
          <FileText size={14} />
          {source.fileName ?? "Trích đoạn tài liệu"}
          {source.sample && <SampleBadge />}
        </span>
        {source.pageStart && (
          <span>
            Trang {source.pageStart}
            {source.pageEnd &&
              source.pageEnd !== source.pageStart &&
              `–${source.pageEnd}`}
          </span>
        )}
      </div>
      {source.fileType === "application/pdf" && url ? (
        <iframe
          title={`Xem trước ${source.fileName}`}
          className="pdf-preview"
          src={`${url}${citation?.page ? `#page=${citation.page}` : ""}`}
        />
      ) : source.text ? (
        <div className={`document-text ${citation ? "is-cited" : ""}`}>
          {source.origin === "demo" && !source.sample ? (
            <Markdown
              remarkPlugins={[remarkGfm]}
              components={{ a: ({ children }) => <span>{children}</span> }}
            >
              {source.text}
            </Markdown>
          ) : (
            <pre className="document-plain">{source.text}</pre>
          )}
        </div>
      ) : (
        <div className="unavailable-preview">
          <FileText size={28} />
          <p>Không xem trước được tệp này.</p>
          <span>Bạn có thể tải tệp gốc về.</span>
        </div>
      )}
      {url && (
        <Button variant="outline" size="sm" asChild>
          <a href={url} download={source.fileName}>
            <Download size={14} />
            Tải tệp gốc
          </a>
        </Button>
      )}
    </div>
  );
}
