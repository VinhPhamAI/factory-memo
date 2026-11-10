import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  BadgeCheck,
  CircleCheck,
  CircleX,
  Database,
  ExternalLink,
  LoaderCircle,
  MessageSquare,
  PencilLine,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import type { CardRow, EvidenceRef, KnowledgeCard } from "@/types";
import { useWorkspace } from "@/hooks/workspace";
import { currentCard } from "@/services/analysis";
import { sleep } from "@/services/timing";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { QuoteBox, RefChip } from "@/components/ref-chip";
import { cn } from "@/utils/cn";

export const CHECKLIST = [
  "Nội dung đúng với bản ghi",
  "Phạm vi máy đúng",
  "An toàn: cảnh báo ngắt nguồn có mặt",
];
const SAVE_STEPS = [
  "Đang lưu vào cơ sở dữ liệu tri thức…",
  "Ghi bảng knowledge_items…",
  "Lập chỉ mục để hỏi đáp…",
];

function shortId(id: string) {
  return `${id.slice(0, 8)}…`;
}
function formatTime(iso?: string) {
  return iso
    ? new Intl.DateTimeFormat("vi", {
        dateStyle: "short",
        timeStyle: "short",
      }).format(new Date(iso))
    : "";
}

/** Card rows with clickable source chips; shared with the chat sources panel. */
export function CardRows({
  card,
  highlight,
  compact = false,
  onOpenRef,
}: {
  card: KnowledgeCard;
  highlight?: string;
  compact?: boolean;
  onOpenRef(ref: EvidenceRef): void;
}) {
  const workspace = useWorkspace();
  const [open, setOpen] = useState<string>();
  const highlighted = useRef<HTMLDivElement>(null);
  useEffect(() => {
    highlighted.current?.scrollIntoView({
      block: compact ? "start" : "center",
      behavior: "instant",
    });
  }, [highlight, compact]);
  const sourceOf = (ref: EvidenceRef) =>
    workspace.sources.find((source) => source.id === ref.sourceId);
  const renderRefs = (key: string, refs: EvidenceRef[]) => {
    const active = refs.find((ref) => `${key}:${ref.label}` === open);
    return (
      <>
        <div className="ref-chips">
          {refs.map((ref) => (
            <RefChip
              key={ref.label}
              evidence={ref}
              testId={`card-ref-${key}-${ref.label}`}
              active={`${key}:${ref.label}` === open}
              onClick={() =>
                setOpen((current) =>
                  current === `${key}:${ref.label}`
                    ? undefined
                    : `${key}:${ref.label}`,
                )
              }
            />
          ))}
        </div>
        {active && (
          <QuoteBox
            evidence={active}
            sourceTitle={sourceOf(active)?.title}
            sample={sourceOf(active)?.sample}
            action={
              <button
                type="button"
                className="link-button"
                data-testid="ref-open-source"
                onClick={() => onOpenRef(active)}
              >
                {active.turnId
                  ? `Mở transcript tại ${active.label}`
                  : "Mở nguồn"}
                <ExternalLink size={13} />
              </button>
            }
          />
        )}
      </>
    );
  };
  return (
    <div className={cn("card-rows", compact && "compact")}>
      {card.rows.map((row: CardRow, index) => (
        <div
          key={row.key}
          ref={row.key === highlight ? highlighted : undefined}
          className={cn("card-row", row.key === highlight && "highlighted")}
          data-testid={`card-row-${row.key}`}
          data-highlighted={row.key === highlight || undefined}
        >
          <div className="card-row-label">
            <span>{index + 1}</span>
            {row.label}
          </div>
          <div className="card-row-body">
            <p>{row.text}</p>
            {renderRefs(row.key, row.refs)}
            {row.key === "cause" && card.ruledOut.length > 0 && (
              <div className="ruled-out" data-testid="card-ruled-out">
                <span className="ruled-out-title">Đã loại trừ</span>
                {card.ruledOut.map((item) => (
                  <div key={item.text} className="ruled-out-item">
                    <CircleX size={15} />
                    <span>
                      <strong>{item.text}</strong> — {item.note}
                    </span>
                    <span className="ruled-out-refs">
                      {item.refs.map((ref) => ref.label).join(" → ")}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export function StatusBadge({ card }: { card?: KnowledgeCard }) {
  const approved = card?.status === "approved";
  return (
    <span
      className={cn("status-badge", approved ? "approved" : "draft")}
      data-testid="card-status"
      data-status={approved ? "approved" : "draft"}
    >
      {approved ? <BadgeCheck size={14} /> : <PencilLine size={13} />}
      {approved ? "Đã duyệt" : "Nháp — chờ duyệt"}
    </span>
  );
}

export function CardPage() {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const card = currentCard(workspace.cards);
  const [checklist, setChecklist] = useState<boolean[]>();
  const [comment, setComment] = useState<string>();
  const [saving, setSaving] = useState(-1);
  const [notice, setNotice] = useState("");
  const checks = checklist ?? card?.checklist ?? [false, false, false];
  const note = comment ?? card?.comment ?? "";
  const approved = card?.status === "approved";

  if (!card)
    return (
      <main className="card-page">
        <div className="route-empty tech-hero" data-testid="card-empty">
          <span className="record-idle-icon">
            <Sparkles size={34} strokeWidth={1.5} />
          </span>
          <h1 className="page-heading">
            {workspace.loading ? "Đang tải…" : "Chưa có knowledge card"}
          </h1>
          <p>Chạy phân tích AI trên bản ghi âm và tài liệu để tạo card.</p>
          <Button onClick={() => void navigate("/analyze")}>
            Phân tích bằng AI
            <ArrowRight size={16} />
          </Button>
        </div>
      </main>
    );

  async function approve() {
    if (!card) return;
    try {
      for (let i = 0; i < SAVE_STEPS.length; i++) {
        setSaving(i);
        await sleep(650);
      }
      await workspace.saveCard({
        ...card,
        status: "approved",
        checklist: checks,
        comment: note.trim(),
        changesRequested: false,
        approvedBy: card.reviewer,
        approvedAt: new Date().toISOString(),
      });
    } finally {
      setSaving(-1);
    }
  }
  async function requestChanges() {
    if (!card) return;
    await workspace.saveCard({
      ...card,
      checklist: checks,
      comment: note.trim(),
      changesRequested: true,
    });
    setNotice("Đã gửi yêu cầu sửa cho junior_01. Card vẫn ở trạng thái Nháp.");
  }

  return (
    <main className="card-page">
      <div className="card-layout">
        <article className="knowledge-card" data-testid="knowledge-card">
          <header className="knowledge-card-head">
            <div>
              <span className="eyebrow">BƯỚC 4 · KNOWLEDGE CARD</span>
              <h1 className="page-heading">{card.subject} · Case</h1>
              <div className="scope-tags">
                {card.scope.map((chip) => (
                  <span key={chip} className="scope-tag">
                    {chip}
                  </span>
                ))}
              </div>
            </div>
            <div className="knowledge-card-status">
              <StatusBadge card={card} />
              <span className="verified-line">
                <CircleCheck size={14} />
                {card.verified.matched}/{card.verified.total} trích dẫn khớp bản
                ghi gốc
              </span>
            </div>
          </header>
          <CardRows
            card={card}
            highlight={params.get("row") ?? undefined}
            onOpenRef={(ref) =>
              void navigate(
                `/sources/${ref.sourceId}${ref.turnId ? `?turn=${ref.label}` : ""}`,
              )
            }
          />
        </article>

        <aside className="review-panel" data-testid="review-panel">
          <div className="review-panel-head">
            <ShieldCheck size={19} />
            <h2>Chuyên gia duyệt</h2>
          </div>
          <div className="reviewer">
            <span className="reviewer-avatar">
              <UserRound size={18} />
            </span>
            <span>
              <strong>expert_01</strong>
              <span>Kỹ thuật viên cao cấp · Line 11</span>
            </span>
          </div>
          <div className="review-checklist">
            {CHECKLIST.map((label, i) => (
              <label key={label} className={cn(checks[i] && "checked")}>
                <input
                  type="checkbox"
                  data-testid={`review-check-${i + 1}`}
                  checked={checks[i]}
                  disabled={approved || saving >= 0}
                  onChange={(e) =>
                    setChecklist(
                      checks.map((value, j) =>
                        j === i ? e.target.checked : value,
                      ),
                    )
                  }
                />
                {label}
              </label>
            ))}
          </div>
          <label className="field-label" htmlFor="review-comment">
            Nhận xét (không bắt buộc)
          </label>
          <Textarea
            id="review-comment"
            data-testid="review-comment"
            rows={3}
            value={note}
            disabled={approved || saving >= 0}
            placeholder="Ví dụ: đã kiểm tra lại với biên bản họp"
            onChange={(e) => setComment(e.target.value)}
          />
          {notice && !approved && (
            <p className="review-notice" role="status">
              {notice}
            </p>
          )}
          {approved ? (
            <div className="saved-box" data-testid="card-approved">
              <strong>
                <Database size={17} />
                Đã lưu · knowledge_items · id {shortId(card.id)}
              </strong>
              <span>
                Duyệt bởi {card.approvedBy} · {formatTime(card.approvedAt)}
              </span>
              <Button
                data-testid="card-ask-cta"
                onClick={() => void navigate("/ask")}
              >
                <MessageSquare size={16} />
                Hỏi đáp với tri thức này
                <ArrowRight size={16} />
              </Button>
            </div>
          ) : saving >= 0 ? (
            <div className="saving-box" data-testid="card-saving">
              <LoaderCircle size={18} className="animate-spin" />
              <span>{SAVE_STEPS[saving]}</span>
              <span className="progress-track">
                <span
                  style={{
                    width: `${((saving + 1) / SAVE_STEPS.length) * 100}%`,
                  }}
                />
              </span>
            </div>
          ) : (
            <div className="review-actions">
              <Button
                variant="outline"
                data-testid="review-request-changes"
                onClick={() => void requestChanges()}
              >
                <PencilLine size={15} />
                Yêu cầu sửa
              </Button>
              <Button
                data-testid="review-approve"
                disabled={!checks.every(Boolean)}
                onClick={() => void approve()}
              >
                <BadgeCheck size={16} />
                Duyệt & lưu vào CSDL
              </Button>
            </div>
          )}
          {!approved && saving < 0 && !checks.every(Boolean) && (
            <p className="field-note">Đánh dấu đủ 3 mục kiểm tra để duyệt.</p>
          )}
        </aside>
      </div>
    </main>
  );
}
