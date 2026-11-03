import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";

/**
 * The model's reasoning, collapsed by default like other chat assistants.
 *
 * While the model is still thinking (no answer text yet) the label says so;
 * afterwards it shows how long the thinking took. The reasoning is unverified,
 * is not part of the answer and is not saved with the conversation.
 */
export function ThinkingBlock({
  reasoning,
  thinking,
  seconds,
}: {
  reasoning: string;
  thinking: boolean;
  seconds?: number;
}) {
  const [open, setOpen] = useState(false);
  const label = thinking
    ? "Đang suy nghĩ…"
    : `Đã suy nghĩ trong ${Math.max(1, seconds ?? 1)} giây`;
  return (
    <div className="thinking-block" data-testid="thinking-block">
      <button
        type="button"
        className="thinking-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {thinking ? (
          <Loader2 size={14} className="animate-spin" />
        ) : open ? (
          <ChevronDown size={14} />
        ) : (
          <ChevronRight size={14} />
        )}
        {label}
      </button>
      {open && (
        <div className="thinking-body">
          <p className="thinking-text">{reasoning}</p>
          <p className="field-note">
            Suy nghĩ nháp của mô hình, chưa được kiểm chứng và không được lưu.
            Chỉ câu trả lời bên dưới là kết quả.
          </p>
        </div>
      )}
    </div>
  );
}
