import { useEffect, useRef, useState } from "react";
import { Download, FileText, Mic } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation, LocatedReference, Source } from "@/types";
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
  location,
  compact = false,
}: {
  source: Source;
  citation?: Pick<Citation, "turnId" | "page" | "excerpt">;
  location?: LocatedReference;
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
      {location ? (
        <LocatedEvidencePreview
          reference={location}
          url={url}
          contentType={source.fileType}
        />
      ) : source.fileType === "application/pdf" && url ? (
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

export function LocatedEvidencePreview({
  reference,
  url,
  contentType,
  renderedPage = false,
}: {
  reference: LocatedReference;
  url?: string;
  contentType?: string;
  renderedPage?: boolean;
}) {
  if (reference.type === "source" && reference.location_missing)
    return (
      <div className="located-evidence missing" role="status">
        <strong>Không xác định được vị trí</strong>
        {reference.quote && <p>{reference.quote}</p>}
      </div>
    );
  if (reference.type === "paragraph")
    return (
      <div className="located-evidence">
        <strong>
          {reference.table && reference.cell
            ? `Bảng ${reference.table}, ô ${reference.cell}`
            : `Đoạn ${reference.index}`}
        </strong>
        {reference.quote && <p>{reference.quote}</p>}
      </div>
    );
  if (reference.type === "cells")
    return (
      <div className="located-evidence">
        <strong>{reference.sheet} · {reference.range}</strong>
        {reference.quote && <p>{reference.quote}</p>}
      </div>
    );
  if ((reference.type === "page" || reference.type === "image") && url) {
    if (contentType === "application/pdf" && !renderedPage)
      return (
        <div className="located-evidence">
          <strong>Trang {reference.page}</strong>
          <iframe
            title={`Bằng chứng trang ${reference.page}`}
            className="pdf-preview"
            src={`${url}#page=${reference.page}`}
          />
          {reference.bbox && (
            <p className="field-note">Vùng: {reference.bbox.join(", ")}</p>
          )}
        </div>
      );
    const box = reference.bbox;
    const width = reference.page_width;
    const height = reference.page_height;
    const style = box && width && height ? {
      left: `${(box[0] / width) * 100}%`,
      top: `${(box[1] / height) * 100}%`,
      width: `${((box[2] - box[0]) / width) * 100}%`,
      height: `${((box[3] - box[1]) / height) * 100}%`,
    } : undefined;
    return (
      <div className="located-evidence">
        <strong>Vùng ảnh</strong>
        <div className="located-image-frame">
          <img src={url} alt="Bằng chứng trong file gốc" />
          {style && <span className="located-image-box" style={style} />}
        </div>
      </div>
    );
  }
  return (
    <div className="located-evidence">
      {"page" in reference && <strong>Trang {reference.page}</strong>}
      {reference.quote && <p>{reference.quote}</p>}
    </div>
  );
}
