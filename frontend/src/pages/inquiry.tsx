import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Paperclip } from "lucide-react";
import { useAuth, useAuthClient } from "@/hooks/auth";
import { useInquiriesApi } from "@/hooks/inquiries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ToastStack, useToasts } from "@/components/ui/toast";
import { EvidenceList } from "@/components/inquiry/evidence-list";
import {
  FIELD_LABELS,
  InquiryForm,
  type TextFields,
} from "@/components/inquiry/inquiry-form";
import { ReviewActions } from "@/components/inquiry/review-actions";
import { Timeline } from "@/components/inquiry/timeline";
import { listFiles, type LibraryFile } from "@/services/file-api";
import {
  allowedActions,
  InquiryError,
  STATUS_LABELS,
  type EventKind,
  type EvidenceItem,
  type Expert,
  type Inquiry,
} from "@/services/inquiries-api";

const messageOf = (error: unknown) =>
  error instanceof InquiryError
    ? error.message
    : "Thao tác thất bại. Hãy thử lại.";

const fieldsOf = (inquiry: Inquiry): TextFields => ({
  situation: inquiry.context.situation,
  observations: inquiry.context.observations,
  attempted_actions: inquiry.context.attempted_actions,
  proposed_fix: inquiry.context.proposed_fix,
});

/** One inquiry: an editable draft for its requester, a read-only record otherwise. */
export function InquiryPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const api = useInquiriesApi();
  const auth = useAuth();
  const userId = auth.status === "signed-in" ? auth.user.id : "";
  const toasts = useToasts();
  const [inquiry, setInquiry] = useState<Inquiry | null>(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [fields, setFields] = useState<TextFields>({
    situation: "",
    observations: "",
    attempted_actions: "",
    proposed_fix: "",
  });
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [experts, setExperts] = useState<Expert[]>([]);
  const [expertId, setExpertId] = useState("");
  const [library, setLibrary] = useState<LibraryFile[] | null>(null);

  const adopt = useCallback((next: Inquiry) => {
    setInquiry(next);
    setTitle(next.title);
    setFields(fieldsOf(next));
    setEvidence(next.context.evidence);
  }, []);

  const load = useCallback(async () => {
    try {
      adopt(await api.get(id));
      setLoadError("");
    } catch (error) {
      setLoadError(messageOf(error));
    }
  }, [api, id, adopt]);

  useEffect(() => {
    void load();
  }, [load]);

  const isDraft = inquiry?.status === "draft";
  const editable = isDraft && inquiry?.requested_by === userId;

  useEffect(() => {
    if (!editable) return;
    api
      .listExperts()
      .then((items) => setExperts(items.filter((item) => item.id !== userId)))
      .catch((error: unknown) => toasts.show("error", messageOf(error)));
    // toasts.show is stable; only reload experts when the draft changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, editable, userId]);

  /** Run an action; a 409 reloads the inquiry and says the status changed. */
  async function run(action: () => Promise<void>, success?: string) {
    setBusy(true);
    try {
      await action();
      if (success) toasts.show("success", success);
    } catch (error) {
      toasts.show("error", messageOf(error));
      if (error instanceof InquiryError && error.status === 409) await load();
    } finally {
      setBusy(false);
    }
  }

  const context = () => ({ ...fields, evidence });
  const save = async () => {
    adopt(await api.update(id, { title, context: context() }));
  };

  async function download(fileId: string) {
    const file = inquiry?.files.find((item) => item.file_id === fileId);
    if (!file) return;
    try {
      await api.downloadFile(id, file);
    } catch (error) {
      toasts.show("error", messageOf(error));
    }
  }

  /** Detach a file; keep unsaved form edits and drop only that file's evidence. */
  async function detach(fileId: string) {
    await api.detachFile(id, fileId);
    // The server already dropped this evidence; mirror it before the refresh can fail.
    setEvidence((current) => current.filter((item) => item.file_id !== fileId));
    setInquiry(await api.get(id));
  }

  const authClient = useAuthClient();

  async function openPicker() {
    try {
      setLibrary(await listFiles(authClient));
    } catch {
      toasts.show("error", "Không tải được thư viện file.");
    }
  }

  if (loadError && !inquiry)
    return (
      <main className="files-page">
        <p role="alert" className="inline-error">
          {loadError}
        </p>
        <Link to="/inquiries">Về danh sách</Link>
        <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
      </main>
    );
  if (!inquiry)
    return (
      <main className="files-page" aria-busy="true">
        Đang tải…
      </main>
    );

  const actions = allowedActions(inquiry, userId);
  const referenced = new Set(evidence.map((item) => item.file_id));
  const attachedIds = new Set(inquiry.files.map((file) => file.file_id));

  return (
    <main className="files-page" data-testid="inquiry-page">
      <p>
        <Link to="/inquiries">← Yêu cầu chuyên gia</Link>
      </p>
      <h1>{inquiry.title || "(Chưa có tiêu đề)"}</h1>
      <p className="field-note">
        <Badge variant="secondary" data-testid="inquiry-status">
          {STATUS_LABELS[inquiry.status] ?? inquiry.status}
        </Badge>{" "}
        Người gửi: {inquiry.requester_name}
        {inquiry.assignee_name && ` · Chuyên gia: ${inquiry.assignee_name}`}
      </p>

      {editable ? (
        <>
          <InquiryForm
            title={title}
            fields={fields}
            onTitle={setTitle}
            onField={(name, value) =>
              setFields((current) => ({ ...current, [name]: value }))
            }
          />
          <h2>Bằng chứng</h2>
          <EvidenceList
            items={evidence}
            onDownload={(fileId) => void download(fileId)}
            onRemove={(index) => {
              const item = evidence[index];
              const rest = evidence.filter((_, i) => i !== index);
              void run(async () => {
                if (item.kind === "file" && item.file_id) {
                  // The server also removes evidence that refers to the file.
                  await detach(item.file_id);
                } else setEvidence(rest);
              });
            }}
          />
          <h3>Tệp đính kèm</h3>
          <ul>
            {inquiry.files
              .filter((file) => !referenced.has(file.file_id))
              .map((file) => (
                <li key={file.file_id}>
                  {file.filename}{" "}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void run(() => detach(file.file_id))
                    }
                  >
                    Gỡ
                  </Button>
                </li>
              ))}
          </ul>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void openPicker()}
          >
            <Paperclip size={14} /> Đính kèm file
          </Button>
          {library && (
            <ul data-testid="file-picker">
              {library.length === 0 && <li>Thư viện chưa có file.</li>}
              {library
                .filter((file) => !attachedIds.has(file.id))
                .map((file) => (
                  <li key={file.id}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await api.attachFile(id, file.id);
                          const next: EvidenceItem[] = [
                            ...evidence,
                            {
                              kind: "file",
                              title: file.filename,
                              file_id: file.id,
                            },
                          ];
                          adopt(
                            await api.update(id, {
                              title,
                              context: { ...fields, evidence: next },
                            }),
                          );
                          setLibrary(null);
                        })
                      }
                    >
                      {file.filename}
                    </button>
                  </li>
                ))}
            </ul>
          )}
          <label>
            Chuyên gia
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
          <div className="inquiry-actions">
            <Button
              type="button"
              disabled={busy}
              onClick={() => void run(save, "Đã lưu.")}
            >
              Lưu
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.remove(id);
                  void navigate("/inquiries");
                })
              }
            >
              Xóa nháp
            </Button>
            <Button
              type="button"
              disabled={busy || !expertId}
              onClick={() =>
                void run(async () => {
                  await save();
                  adopt(await api.submit(id, expertId));
                }, "Đã gửi cho chuyên gia.")
              }
            >
              Gửi
            </Button>
          </div>
        </>
      ) : (
        <>
          {FIELD_LABELS.map(([name, label]) => (
            <section key={name}>
              <h2>{label}</h2>
              <p style={{ whiteSpace: "pre-wrap" }}>
                {inquiry.context[name] || "—"}
              </p>
            </section>
          ))}
          <h2>Bằng chứng</h2>
          <EvidenceList
            items={inquiry.context.evidence}
            onDownload={(fileId) => void download(fileId)}
          />
          <h2>Phản hồi</h2>
          <Timeline events={inquiry.events} />
          <ReviewActions
            actions={actions}
            busy={busy}
            onSubmit={(kind: EventKind, content?: string) =>
              void run(async () => {
                adopt(await api.addEvent(id, kind, content));
              }, "Đã ghi nhận.")
            }
          />
        </>
      )}
      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </main>
  );
}
