import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check, Loader2, Pencil, Send, Sparkles, TicketCheck } from "lucide-react";
import { useInquiriesApi } from "@/hooks/inquiries";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Avatar } from "@/components/ticket/avatar";
import { StatusLozenge } from "@/components/ticket/status-lozenge";
import { EvidenceList } from "@/components/inquiry/evidence-list";
import { FIELD_LABELS } from "@/components/inquiry/inquiry-form";
import { ReviewActions } from "@/components/inquiry/review-actions";
import { Timeline } from "@/components/inquiry/timeline";
import {
  allowedActions,
  InquiryError,
  type EventKind,
  type Expert,
  type Inquiry,
} from "@/services/inquiries-api";
import { splitProposal, STAGES, ticketKey } from "@/services/tickets";

/** Shortest time "Đang phân tích…" stays visible, so the demo step reads as work. */
const ANALYZE_MIN_MS = 1500;

const messageOf = (error: unknown) =>
  error instanceof InquiryError
    ? error.message
    : "Thao tác thất bại. Hãy thử lại.";

const dateTime = (iso: string) =>
  new Date(iso).toLocaleString("vi-VN", {
    dateStyle: "short",
    timeStyle: "short",
  });

const pause = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Centered dialog of one ticket. The main column shows the demo AI proposal,
 * the incident description, evidence and replies; the side column shows the
 * next step for the signed-in user (analyze, send to an expert, or review) and
 * the ticket details.
 */
export function TicketDialog({
  ticketId,
  userId,
  onClose,
  onChanged,
  notify,
}: {
  ticketId: string | null;
  userId: string;
  onClose: () => void;
  /** Called after an action changed the ticket, so the board reloads. */
  onChanged: () => void;
  notify: (kind: "success" | "error", message: string) => void;
}) {
  const api = useInquiriesApi();
  const [ticket, setTicket] = useState<Inquiry | null>(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState<"" | "analyze" | "other">("");
  const [experts, setExperts] = useState<Expert[]>([]);
  const [expertId, setExpertId] = useState("");

  const load = useCallback(async () => {
    if (!ticketId) return;
    try {
      setTicket(await api.get(ticketId));
      setLoadError("");
    } catch (error) {
      setLoadError(messageOf(error));
    }
  }, [api, ticketId]);

  useEffect(() => {
    setTicket(null);
    setLoadError("");
    setExpertId("");
    void load();
  }, [load]);

  const editable = ticket?.status === "draft" && ticket.requested_by === userId;

  useEffect(() => {
    if (!editable) return;
    api
      .listExperts()
      .then((items) => setExperts(items.filter((item) => item.id !== userId)))
      .catch((error: unknown) => notify("error", messageOf(error)));
  }, [api, editable, userId, notify]);

  /** Run an action, adopt the returned ticket and tell the board; a 409 reloads. */
  async function run(
    kind: "analyze" | "other",
    action: () => Promise<Inquiry>,
    success: string,
  ) {
    setBusy(kind);
    try {
      setTicket(await action());
      notify("success", success);
      onChanged();
    } catch (error) {
      notify("error", messageOf(error));
      if (error instanceof InquiryError && error.status === 409) await load();
    } finally {
      setBusy("");
    }
  }

  const stage = ticket?.stage ?? null;
  const { own, proposal } = splitProposal(ticket?.context.proposed_fix ?? "");

  const actions = ticket ? allowedActions(ticket, userId) : [];

  return (
    <Dialog open={ticketId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="ticket-dialog gap-0 p-0 sm:max-w-5xl"
        data-testid="ticket-dialog"
      >
        <DialogHeader className="ticket-dialog-header">
          <DialogDescription asChild>
            <div className="ticket-dialog-crumb">
              <span className="ticket-key">
                <TicketCheck size={14} aria-hidden="true" />
                Ticket sự cố
                {ticket && ` / ${ticketKey(ticket.id)}`}
              </span>
              {ticket && <StatusLozenge status={ticket.status} />}
            </div>
          </DialogDescription>
          <DialogTitle className="ticket-dialog-title">
            {ticket ? ticket.title || "(Chưa có tiêu đề)" : "Ticket"}
          </DialogTitle>
          {stage && (
            <ol
              className="ticket-steps"
              data-testid="ticket-stage"
              aria-label="Tiến trình ticket"
            >
              {STAGES.map((item, index) => {
                const at = STAGES.findIndex((entry) => entry.id === stage);
                const state =
                  index < at ? "past" : index === at ? "current" : "next";
                return (
                  <li
                    key={item.id}
                    data-state={state}
                    aria-current={state === "current" ? "step" : undefined}
                  >
                    <span className="ticket-step-mark" aria-hidden="true">
                      {state === "past" ? <Check size={12} /> : index + 1}
                    </span>
                    {item.hint}
                  </li>
                );
              })}
            </ol>
          )}
        </DialogHeader>

        <div className="ticket-dialog-body">
          {loadError && (
            <p role="alert" className="inline-error">
              {loadError}
            </p>
          )}
          {!ticket && !loadError && (
            <p className="field-note" aria-busy="true">
              Đang tải…
            </p>
          )}
          {ticket && (
            <>
              <div className="ticket-dialog-main">
                <section
                  className="ticket-proposal"
                  data-testid="ticket-proposal"
                  aria-label="Đề xuất AI"
                >
                  <h2>
                    <Sparkles size={15} aria-hidden="true" /> Đề xuất AI
                    {proposal && busy !== "analyze" && (
                      <span className="ticket-proposal-note">
                        Bản demo — chưa được chuyên gia xác nhận
                      </span>
                    )}
                  </h2>
                  {busy === "analyze" ? (
                    <p className="field-note" aria-busy="true">
                      <Loader2 size={14} className="inline animate-spin" /> Đang
                      phân tích tài liệu và kinh nghiệm liên quan…
                    </p>
                  ) : proposal ? (
                    <p className="ticket-proposal-text">{proposal}</p>
                  ) : (
                    <p className="field-note">
                      {editable
                        ? "Chưa có đề xuất. Bấm “Phân tích” để tạo."
                        : "Không có đề xuất AI."}
                    </p>
                  )}
                </section>

                <h2 className="ticket-section-title">Mô tả sự cố</h2>
                <dl className="ticket-fields">
                  {FIELD_LABELS.map(([name, label]) => {
                    const text =
                      name === "proposed_fix" ? own : ticket.context[name];
                    return (
                      <div key={name}>
                        <dt>{label}</dt>
                        <dd data-empty={text ? undefined : true}>
                          {text || "Chưa có nội dung"}
                        </dd>
                      </div>
                    );
                  })}
                </dl>

                <h2 className="ticket-section-title">
                  Bằng chứng
                  <span className="ticket-count">
                    {ticket.context.evidence.length}
                  </span>
                </h2>
                <EvidenceList items={ticket.context.evidence} />

                {ticket.status !== "draft" && (
                  <>
                    <h2 className="ticket-section-title">
                      Lịch sử phản hồi
                      <span className="ticket-count">
                        {ticket.events.length}
                      </span>
                    </h2>
                    <Timeline events={ticket.events} />
                  </>
                )}
              </div>

              <aside className="ticket-dialog-side">
                {editable && (
                  <section className="ticket-panel" aria-label="Việc tiếp theo">
                    <h2>Việc tiếp theo</h2>
                    <Button
                      type="button"
                      variant={proposal ? "outline" : "default"}
                      disabled={busy !== ""}
                      data-testid="ticket-analyze"
                      onClick={() =>
                        void run(
                          "analyze",
                          async () =>
                            (
                              await Promise.all([
                                api.demoProposal(ticket.id),
                                pause(ANALYZE_MIN_MS),
                              ])
                            )[0],
                          "Đã có đề xuất.",
                        )
                      }
                    >
                      {busy === "analyze" ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Sparkles size={14} />
                      )}
                      {busy === "analyze"
                        ? "Đang phân tích…"
                        : proposal
                          ? "Phân tích lại"
                          : "Phân tích"}
                    </Button>
                    <label className="ticket-expert">
                      Gửi chuyên gia duyệt
                      <select
                        aria-label="Chuyên gia"
                        value={expertId}
                        onChange={(event) => setExpertId(event.target.value)}
                      >
                        <option value="">Chọn chuyên gia…</option>
                        {experts.map((expert) => (
                          <option key={expert.id} value={expert.id}>
                            {expert.display_name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <Button
                      type="button"
                      variant={proposal ? "default" : "outline"}
                      disabled={busy !== "" || !expertId}
                      data-testid="ticket-submit"
                      onClick={() =>
                        void run(
                          "other",
                          () => api.submit(ticket.id, expertId),
                          "Đã gửi cho chuyên gia.",
                        )
                      }
                    >
                      <Send size={14} /> Gửi chuyên gia
                    </Button>
                    <Button asChild variant="ghost">
                      <Link to={`/inquiries/${ticket.id}`}>
                        <Pencil size={14} /> Sửa nội dung
                      </Link>
                    </Button>
                  </section>
                )}

                {actions.length > 0 && (
                  <div className="ticket-panel">
                    <ReviewActions
                      actions={actions}
                      busy={busy !== ""}
                      onSubmit={(kind: EventKind, content?: string) =>
                        void run(
                          "other",
                          () => api.addEvent(ticket.id, kind, content),
                          "Đã ghi nhận.",
                        )
                      }
                    />
                  </div>
                )}

                {!editable && actions.length === 0 && (
                  <p className="ticket-panel ticket-panel-note">
                    {stage === "done"
                      ? "Ticket đã hoàn tất."
                      : ticket.assignee_name
                        ? `Đang chờ ${ticket.assignee_name} phản hồi.`
                        : "Không có việc cần làm."}
                  </p>
                )}

                <section className="ticket-panel" aria-label="Chi tiết">
                  <h2>Chi tiết</h2>
                  <dl className="ticket-details">
                    <dt>Trạng thái</dt>
                    <dd>
                      <StatusLozenge status={ticket.status} />
                    </dd>
                    <dt>Người gửi</dt>
                    <dd>
                      <Avatar name={ticket.requester_name} />
                      {ticket.requester_name}
                    </dd>
                    <dt>Chuyên gia</dt>
                    <dd data-empty={ticket.assignee_name ? undefined : true}>
                      {ticket.assignee_name ? (
                        <>
                          <Avatar name={ticket.assignee_name} />
                          {ticket.assignee_name}
                        </>
                      ) : (
                        "Chưa giao"
                      )}
                    </dd>
                    <dt>Tạo lúc</dt>
                    <dd>{dateTime(ticket.created_at)}</dd>
                    {ticket.submitted_at && (
                      <>
                        <dt>Gửi lúc</dt>
                        <dd>{dateTime(ticket.submitted_at)}</dd>
                      </>
                    )}
                    <dt>Cập nhật</dt>
                    <dd>{dateTime(ticket.updated_at)}</dd>
                  </dl>
                </section>
              </aside>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
