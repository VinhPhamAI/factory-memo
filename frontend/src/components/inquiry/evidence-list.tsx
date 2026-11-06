import { Download, Quote, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { EvidenceItem } from "@/services/inquiries-api";

/** Evidence of an inquiry: citation excerpts as text, files as downloads. */
export function EvidenceList({
  items,
  onRemove,
  onDownload,
}: {
  items: EvidenceItem[];
  /** Given on editable drafts only. */
  onRemove?: (index: number) => void;
  onDownload?: (fileId: string) => void;
}) {
  if (!items.length)
    return (
      <p className="field-note" data-testid="evidence-empty">
        Chưa có bằng chứng
      </p>
    );
  return (
    <ul className="inquiry-evidence">
      {items.map((item, index) => (
        <li key={index} data-testid="evidence-item">
          <div>
            <strong>
              {item.kind === "citation" && <Quote size={13} />} {item.title}
            </strong>
            {item.location && (
              <span className="field-note"> · {item.location}</span>
            )}
            {item.excerpt && (
              <p style={{ whiteSpace: "pre-wrap" }}>{item.excerpt}</p>
            )}
          </div>
          {item.kind === "file" && item.file_id && onDownload && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onDownload(item.file_id!)}
            >
              <Download size={14} /> Tải về
            </Button>
          )}
          {onRemove && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Xóa bằng chứng ${item.title}`}
              onClick={() => onRemove(index)}
            >
              <X size={14} />
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}
