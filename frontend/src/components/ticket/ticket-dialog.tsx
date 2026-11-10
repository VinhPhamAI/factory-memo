import { useCallback, useEffect, useState } from "react";
import {
  Check,
  Loader2,
  Pencil,
  Save,
  Send,
  Sparkles,
  TicketCheck,
  X,
} from "lucide-react";
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
import {
  FIELD_LABELS,
  InquiryForm,
  type TextFields,
} from "@/components/inquiry/inquiry-form";
import { ReviewActions } from "@/components/inquiry/review-actions";
import { Timeline } from "@/components/inquiry/timeline";
import {
  allowedActions,
  InquiryError,
  type EventKind,
  type Expert,
  type Inquiry,
} from "@/services/inquiries-api";
import {
  analyzeTicket,
  joinProposal,
  splitProposal,
  STAGES,
  ticketKey,
} from "@/services/tickets";

const messageOf = (error: unknown) =>
  error instanceof InquiryError
    ? error.message
    : "Thao tác thất bại. Hãy thử lại.";

/** Form values of an edit: the title and the four text fields (own fix only). */
type Draft = { title: string; fields: TextFields };

const dateTime = (iso: string) =>
  new Date(iso).toLocaleString("vi-VN", {
    dateStyle: "short",
    timeStyle: "short",
  });

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
  /** Unsaved form values while the ticket is being edited; null otherwise. */
  const [draft, setDraft] = useState<Draft | null>(null);

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
    setDraft(null);
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

  /**
   * Run an action, adopt the returned ticket and tell the board; a 409 reloads.
   * Resolves to whether the action succeeded.
   */
  async function run(
    kind: "analyze" | "other",
    action: () => Promise<Inquiry>,
    success: string,
  ): Promise<boolean> {
    setBusy(kind);
    try {
      setTicket(await action());
      notify("success", success);
      onChanged();
      return true;
    } catch (error) {
      notify("error", messageOf(error));
      if (error instanceof InquiryError && error.status === 409) {
        setDraft(null);
        await load();
      }
      return false;
    } finally {
      setBusy("");
    }
  }

  const stage = ticket?.stage ?? null;
  const { own, proposal } = splitProposal(ticket?.context.proposed_fix ?? "");

  function startEdit() {
    if (!ticket) return;
    setDraft({
      title: ticket.title,
      fields: { ...ticket.context, proposed_fix: own },
    });
  }

  /** Save the form through the same PATCH as the inquiry page; keep the AI proposal. */
  async function saveEdit() {
    if (!ticket || !draft) return;
    const saved = await run(
      "other",
      () =>
        api.update(ticket.id, {
          title: draft.title,
          context: {
            ...ticket.context,
            ...draft.fields,
            proposed_fix: joinProposal(draft.fields.proposed_fix, proposal),
          },
        }),
      "Đã lưu.",
    );
    if (saved) setDraft(null);
  }

  const actions = ticket ? allowedActions(ticket, userId) : [];

  return (
    <Dialog
      open={ticketId !== null}
      onOpenChange={(open) => !open && onClose()}
    >
      <DialogContent
        className="ticket-dialog gap-0 p-0 sm:max-w-5xl"
        data-testid="ticket-dialog"
      >
        <DialogHeader className="ticket-dialog-header">
          <DialogDescription asChild>
            <div className="ticket-dialog-crumb">
              <span className="ticket-key">
                <TicketCheck size={14} aria-hidden="true" />
                Xử lý sự cố
                {ticket && ` / ${ticketKey(ticket.id)}`}
              </span>
              {ticket && <StatusLozenge status={ticket.status} />}
            </div>
          </DialogDescription>
          <DialogTitle className="ticket-dialog-title">
            {ticket ? ticket.title || "(Chưa có tiêu đề)" : "Ticket"}
          </DialogTitle>
        </DialogHeader>
        {stage && (
          <div className="ticket-steps-bar">
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
          </div>
        )}

        <div className="ticket-dialog-body">
          {loadError && (
            <p role="alert" className="inline-error">
              {loadError}
            </p>
          )}
          {!ticket && !loadError && (
            <>
              <div className="ticket-dialog-main" aria-busy="true">
                <span className="sr-only">Đang tải…</span>
                <div className="ticket-skeleton-block" aria-hidden="true">
                  <span className="ticket-skeleton" data-width="short" />
                  <span className="ticket-skeleton" data-width="full" />
                  <span className="ticket-skeleton" data-width="full" />
                  <span className="ticket-skeleton" data-width="mid" />
                </div>
                <div className="ticket-skeleton-block" aria-hidden="true">
                  <span className="ticket-skeleton" data-width="short" />
                  <span className="ticket-skeleton" data-width="full" />
                  <span className="ticket-skeleton" data-width="mid" />
                </div>
              </div>
              <aside className="ticket-dialog-side" aria-hidden="true">
                <div className="ticket-panel">
                  <span className="ticket-skeleton" data-width="mid" />
                  <span className="ticket-skeleton" data-width="full" />
                  <span className="ticket-skeleton" data-width="full" />
                </div>
              </aside>
            </>
          )}
          {ticket && (
            <>
              <div className="ticket-dialog-main">
                <section
                  className="ticket-proposal"
                  data-testid="ticket-proposal"
                  data-busy={busy === "analyze" || undefined}
                  aria-label="Đề xuất AI"
                >
                  <h2>
                    <Sparkles size={15} aria-hidden="true" /> Đề xuất AI
                    {proposal && busy !== "analyze" && (
                      <span className="ticket-proposal-note">
                        Chưa được chuyên gia xác nhận
                      </span>
                    )}
                  </h2>
                  {busy === "analyze" ? (
                    <div className="ticket-analyzing" aria-busy="true">
                      <p className="field-note">
                        <Loader2 size={14} className="inline animate-spin" />{" "}
                        Đang phân tích tài liệu và kinh nghiệm liên quan…
                      </p>
                      <span className="ticket-skeleton" data-width="full" />
                      <span className="ticket-skeleton" data-width="full" />
                      <span className="ticket-skeleton" data-width="mid" />
                    </div>
                  ) : proposal ? (
                    // The key replays the reveal when a new proposal arrives.
                    <p key={proposal} className="ticket-proposal-text">
                      {proposal}
                    </p>
                  ) : (
                    <p className="field-note">
                      {editable
                        ? "Chưa có đề xuất. Bấm “Phân tích” để tạo."
                        : "Không có đề xuất AI."}
                    </p>
                  )}
                </section>

                <section className="ticket-section" aria-label="Mô tả sự cố">
                  <div className="ticket-section-head">
                    <h2 className="ticket-section-title">Mô tả sự cố</h2>
                    {editable &&
                      (draft ? (
                        <div className="ticket-section-actions">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy !== ""}
                            data-testid="ticket-cancel-edit"
                            onClick={() => setDraft(null)}
                          >
                            <X size={14} /> Hủy
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            disabled={busy !== ""}
                            data-testid="ticket-save"
                            onClick={() => void saveEdit()}
                          >
                            {busy === "other" ? (
                              <Loader2 size={14} className="animate-spin" />
                            ) : (
                              <Save size={14} />
                            )}
                            Lưu
                          </Button>
                        </div>
                      ) : (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={busy !== ""}
                          data-testid="ticket-edit"
                          onClick={startEdit}
                        >
                          <Pencil size={14} /> Sửa nội dung
                        </Button>
                      ))}
                  </div>
                  {draft ? (
                    <div className="ticket-edit" data-testid="ticket-edit-form">
                      <InquiryForm
                        title={draft.title}
                        fields={draft.fields}
                        onTitle={(title) =>
                          setDraft(
                            (current) => current && { ...current, title },
                          )
                        }
                        onField={(name, value) =>
                          setDraft(
                            (current) =>
                              current && {
                                ...current,
                                fields: { ...current.fields, [name]: value },
                              },
                          )
                        }
                      />
                      {proposal && (
                        <p className="field-note">
                          Đề xuất AI ở trên được giữ nguyên khi lưu.
                        </p>
                      )}
                    </div>
                  ) : (
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
                  )}
                </section>

                <section className="ticket-section" aria-label="Bằng chứng">
                  <div className="ticket-section-head">
                    <h2 className="ticket-section-title">
                      Bằng chứng
                      <span className="ticket-count">
                        {ticket.context.evidence.length}
                      </span>
                    </h2>
                  </div>
                  <EvidenceList items={ticket.context.evidence} />
                </section>

                {ticket.status !== "draft" && (
                  <section
                    className="ticket-section"
                    aria-label="Lịch sử phản hồi"
                  >
                    <div className="ticket-section-head">
                      <h2 className="ticket-section-title">
                        Lịch sử phản hồi
                        <span className="ticket-count">
                          {ticket.events.length}
                        </span>
                      </h2>
                    </div>
                    <Timeline events={ticket.events} />
                  </section>
                )}
              </div>

              <aside className="ticket-dialog-side">
                {editable && (
                  <section className="ticket-panel" aria-label="Việc tiếp theo">
                    <h2>Việc tiếp theo</h2>
                    {draft && (
                      <p className="field-note">
                        Lưu hoặc hủy chỉnh sửa trước khi tiếp tục.
                      </p>
                    )}
                    <Button
                      type="button"
                      variant={proposal ? "outline" : "default"}
                      disabled={busy !== "" || draft !== null}
                      data-testid="ticket-analyze"
                      onClick={() =>
                        void run(
                          "analyze",
                          () => analyzeTicket(api, ticket.id),
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
                    <div className="ticket-expert">
                      <label>
                        Gửi chuyên gia duyệt
                        <select
                          aria-label="Chuyên gia"
                          value={expertId}
                          disabled={draft !== null}
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
                        disabled={busy !== "" || !expertId || draft !== null}
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
                    </div>
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
                    <div>
                      <dt>Trạng thái</dt>
                      <dd>
                        <StatusLozenge status={ticket.status} />
                      </dd>
                    </div>
                    <div>
                      <dt>Người gửi</dt>
                      <dd>
                        <Avatar name={ticket.requester_name} />
                        {ticket.requester_name}
                      </dd>
                    </div>
                    <div>
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
                    </div>
                    <div>
                      <dt>Tạo lúc</dt>
                      <dd>{dateTime(ticket.created_at)}</dd>
                    </div>
                    {ticket.submitted_at && (
                      <div>
                        <dt>Gửi lúc</dt>
                        <dd>{dateTime(ticket.submitted_at)}</dd>
                      </div>
                    )}
                    <div>
                      <dt>Cập nhật</dt>
                      <dd>{dateTime(ticket.updated_at)}</dd>
                    </div>
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
