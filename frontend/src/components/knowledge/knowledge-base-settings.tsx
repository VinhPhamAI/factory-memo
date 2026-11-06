import { useEffect, useState } from "react";
import { Loader2, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  deleteKnowledgeBase,
  getKnowledgeBase,
  MAX_KB_DESCRIPTION,
  MAX_KB_TITLE,
  updateKnowledgeBase,
  type KnowledgeBaseDetail,
} from "@/services/knowledge-base-admin";
import { dateTime } from "@/services/voice-display";

const sourceStatus = {
  received: "Đã nhận",
  processing: "Đang xử lý",
  processed: "Đã xử lý",
  failed: "Lỗi",
};
const chunkKind = {
  knowledge: "Mục tri thức",
  document_passage: "Đoạn tài liệu",
};
const chunkStatus = { draft: "Nháp", approved: "Đã duyệt", deprecated: "Đã loại" };

function counts(values: Record<string, number>, labels: Record<string, string>) {
  return Object.entries(values)
    .map(([status, count]) => `${labels[status] ?? status} ${count}`)
    .join(" · ");
}

/**
 * Show a knowledge base's contents, edit and delete actions, and its
 * technical details behind a disclosure.
 *
 * Args:
 *   name: Knowledge base ID.
 *   role: Signed-in role; admins and experts may edit, only admins delete.
 *   revision: Changes when the caller wants the details reloaded.
 *   onChanged: Called after a successful edit or delete.
 *   isDefault: Whether new uploads go here; the default cannot be deleted.
 *   compact: Hide the title when the caller already shows it.
 */
export function KnowledgeBaseSettings({
  name,
  role,
  revision,
  onChanged,
  isDefault = false,
  compact = false,
}: {
  name: string;
  role?: string;
  revision: number;
  onChanged(): void;
  isDefault?: boolean;
  compact?: boolean;
}) {
  const [detail, setDetail] = useState<KnowledgeBaseDetail>();
  const [dialog, setDialog] = useState<"edit" | "delete">();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    const abort = new AbortController();
    getKnowledgeBase(name, abort.signal)
      .then((data) => {
        if (abort.signal.aborted) return;
        setDetail(data);
        setLoadError("");
      })
      .catch((error: Error) => {
        if (!abort.signal.aborted) setLoadError(error.message);
      });
    return () => abort.abort();
  }, [name, revision]);

  const canSubmit =
    dialog === "delete" ? confirmation === name : !!dialog && !!title.trim();

  async function submit() {
    if (!canSubmit || busy) return;
    setBusy(true);
    setActionError("");
    try {
      if (dialog === "delete") {
        await deleteKnowledgeBase(name);
      } else {
        setDetail(await updateKnowledgeBase(name, { title, description }));
      }
      setDialog(undefined);
      onChanged();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Thông tin kho kiến thức">
      {loadError && <p className="vc-notice error" role="alert">{loadError}</p>}
      {!detail && !loadError && (
        <p className="vc-muted" role="status">
          <Loader2 className="animate-spin" size={16} /> Đang tải chi tiết…
        </p>
      )}
      {detail && (
        <article className="km-kb-card">
          <header className="km-kb-card-header">
            {!compact && <div>
              <h2>{detail.title}</h2>
              {detail.description && <p>{detail.description}</p>}
            </div>}
            {(role === "admin" || role === "expert") && (
              <div className="km-kb-actions">
                <Button variant="outline" onClick={() => {
                  setTitle(detail.title);
                  setDescription(detail.description);
                  setActionError("");
                  setDialog("edit");
                }}><Pencil /> Sửa thông tin</Button>
                {role === "admin" && (
                  <Button variant="destructive" disabled={isDefault} title={isDefault ? "Chọn kho mặc định khác trước khi xóa" : undefined} onClick={() => {
                    setConfirmation("");
                    setActionError("");
                    setDialog("delete");
                  }}><Trash2 /> Xóa kho</Button>
                )}
              </div>
            )}
          </header>
          {isDefault && role === "admin" && <p className="vc-muted">Chọn một kho mặc định khác trước khi xóa kho này.</p>}
          <div className="km-kb-counts">
            <div>
              <h3>Tài liệu nguồn</h3>
              <p>{counts(detail.source_counts, sourceStatus) || "Chưa có"}</p>
            </div>
            {Object.entries(detail.chunk_counts).map(([kind, values]) => (
              <div key={kind}>
                <h3>{chunkKind[kind as keyof typeof chunkKind] ?? kind}</h3>
                <p>{counts(values, chunkStatus) || "Chưa có"}</p>
              </div>
            ))}
          </div>
          <details className="km-kb-technical">
            <summary>Chi tiết kỹ thuật</summary>
            <dl className="km-kb-metadata">
              <div><dt>Mã</dt><dd><code>{detail.name}</code></dd></div>
              <div><dt>Mẫu</dt><dd>{detail.template}</dd></div>
              <div><dt>Người tạo</dt><dd>{detail.created_by}</dd></div>
              <div><dt>Ngày tạo</dt><dd>{dateTime(detail.created_at)}</dd></div>
              <div><dt>Cập nhật</dt><dd>{dateTime(detail.updated_at)}</dd></div>
            </dl>
          </details>
        </article>
      )}
      <Dialog open={!!dialog} onOpenChange={(open) => {
        if (!open && !busy) setDialog(undefined);
      }}>
        <DialogContent className="vc-kb-dialog">
          <form className="vc-kb-form" onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}>
            <DialogHeader>
              <DialogTitle>{dialog === "delete" ? "Xóa kho kiến thức" : "Sửa thông tin kho"}</DialogTitle>
              <DialogDescription>
                {dialog === "delete"
                  ? `Nhập mã “${name}” để xác nhận xóa. Kho sẽ không còn dùng được; các tác vụ đang chờ sẽ bị dừng. Dữ liệu và lịch sử được giữ lại.`
                  : `Cập nhật thông tin hiển thị cho ${name}.`}
              </DialogDescription>
            </DialogHeader>
            {dialog === "delete" ? (
              <label className="vc-field">
                <span>Mã xác nhận</span>
                <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
              </label>
            ) : (
              <>
                <label className="vc-field">
                  <span>Tiêu đề</span>
                  <input required maxLength={MAX_KB_TITLE} value={title}
                    onChange={(event) => setTitle(event.target.value)} />
                </label>
                <label className="vc-field">
                  <span>Mô tả</span>
                  <textarea maxLength={MAX_KB_DESCRIPTION} value={description}
                    onChange={(event) => setDescription(event.target.value)} />
                </label>
              </>
            )}
            {actionError && <p className="vc-notice error" role="alert">{actionError}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" disabled={busy}
                onClick={() => setDialog(undefined)}>Hủy</Button>
              <Button type="submit" variant={dialog === "delete" ? "destructive" : "default"}
                disabled={busy || !canSubmit}>
                {dialog === "delete" ? "Xác nhận xóa" : "Lưu thay đổi"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  );
}
