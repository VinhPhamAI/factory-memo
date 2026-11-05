import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { EventKind, ReviewAction } from "@/services/inquiries-api";

const ACTIONS: Record<
  ReviewAction,
  { label: string; kind: EventKind; needsContent: boolean; prompt: string }
> = {
  accept: {
    label: "Chấp nhận",
    kind: "accepted",
    needsContent: false,
    prompt: "",
  },
  edit: {
    label: "Sửa giải pháp",
    kind: "edited",
    needsContent: true,
    prompt: "Giải pháp đã sửa",
  },
  deny: {
    label: "Từ chối",
    kind: "denied",
    needsContent: true,
    prompt: "Lý do từ chối",
  },
  question: {
    label: "Hỏi thêm",
    kind: "question",
    needsContent: true,
    prompt: "Câu hỏi cho người gửi",
  },
  reply: {
    label: "Trả lời",
    kind: "reply",
    needsContent: true,
    prompt: "Nội dung trả lời",
  },
};

/** Buttons for the actions the user may take; content actions open a textarea first. */
export function ReviewActions({
  actions,
  busy,
  onSubmit,
}: {
  actions: ReviewAction[];
  busy: boolean;
  onSubmit: (kind: EventKind, content?: string) => void;
}) {
  const [open, setOpen] = useState<ReviewAction | null>(null);
  const [content, setContent] = useState("");
  if (!actions.length) return null;
  const chosen = open ? ACTIONS[open] : null;
  return (
    <section data-testid="review-actions" aria-label="Phản hồi">
      <div className="inquiry-actions">
        {actions.map((action) => (
          <Button
            key={action}
            type="button"
            variant={open === action ? "default" : "outline"}
            disabled={busy}
            onClick={() => {
              const spec = ACTIONS[action];
              if (spec.needsContent) {
                setOpen(action);
                setContent("");
              } else onSubmit(spec.kind);
            }}
          >
            {ACTIONS[action].label}
          </Button>
        ))}
      </div>
      {chosen && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(chosen.kind, content.trim());
          }}
        >
          <Textarea
            aria-label={chosen.prompt}
            placeholder={chosen.prompt}
            value={content}
            maxLength={4000}
            rows={4}
            onChange={(event) => setContent(event.target.value)}
          />
          <Button type="submit" disabled={busy || !content.trim()}>
            Gửi {chosen.label.toLowerCase()}
          </Button>
        </form>
      )}
    </section>
  );
}
