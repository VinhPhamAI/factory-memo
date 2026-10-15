import type { ReactNode } from "react";
import { FileText, Mic } from "lucide-react";
import type { EvidenceRef } from "@/types";
import { cn } from "@/utils/cn";

/** A source chip: a transcript turn ("T10") or a document label. */
export function RefChip({
  evidence,
  active = false,
  onClick,
  testId,
}: {
  evidence: EvidenceRef;
  active?: boolean;
  onClick?(): void;
  testId?: string;
}) {
  const Icon = evidence.turnId ? Mic : FileText;
  const content = (
    <>
      <Icon size={12} />
      {evidence.label}
    </>
  );
  if (!onClick)
    return (
      <span className="ref-chip" data-testid={testId}>
        {content}
      </span>
    );
  return (
    <button
      type="button"
      className={cn("ref-chip", active && "active")}
      aria-pressed={active}
      data-testid={testId}
      onClick={onClick}
    >
      {content}
    </button>
  );
}

export function QuoteBox({
  evidence,
  sourceTitle,
  sample,
  action,
}: {
  evidence: EvidenceRef;
  sourceTitle?: string;
  sample?: boolean;
  action?: ReactNode;
}) {
  return (
    <div className="quote-box" data-testid="ref-quote">
      <p>“{evidence.quote}”</p>
      <div className="quote-box-meta">
        <span>
          {evidence.turnId
            ? `Lượt ${evidence.label}`
            : evidence.page
              ? `Trang ${evidence.page}`
              : "Tài liệu"}
          {sourceTitle && ` · ${sourceTitle}`}
          {sample && <span className="sample-badge">Mẫu</span>}
        </span>
        {action}
      </div>
    </div>
  );
}
